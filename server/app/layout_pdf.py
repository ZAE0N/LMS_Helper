"""모양을 유지한 번역 PDF (PDF 보기·베타).

pdf2zh_next(BabelDOC)는 서버와 PyMuPDF 버전이 달라 같은 가상환경에 넣을 수 없으므로
별도 가상환경의 실행 파일(PDF2ZH_BIN)을 외부 프로그램으로 실행한다.
교안(doc_id)마다 한 번만 만들어 저장하고 모든 학생이 같은 파일을 받는다 → 결과가 매번 같고 토큰도 한 번만 쓴다.
"""
import csv
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from threading import Lock

import pymupdf

from . import glossary as g
from . import service

log = logging.getLogger(__name__)
VERSION = 'v1'  # 아래 옵션·프롬프트를 바꾸면 올려서 새로 만들게 함
RETRY_SECONDS = 10 * 60
TIMEOUT_SECONDS = 30 * 60
PROMPT = ('Korean university lecture slides. Keep code, URLs and English technical terms as they are. '
          'Use short slide-style phrases.')


class LayoutError(Exception):
    pass


def originals_dir() -> Path:
    return service.DATA_DIR / 'originals'


def layout_dir() -> Path:
    return service.DATA_DIR / 'layout'


def save_original(doc_id: str, data: bytes) -> None:
    """PDF 보기용 원본 보관 (data/는 커밋하지 않음)"""
    path = originals_dir() / f'{doc_id}.pdf'
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def remove_title_shadows(src: Path, dst: Path) -> int:
    """글자를 감싸는 작은 그림(제목 그림자·광택 효과)을 지운 사본. 글자 자체는 따로 있음.
    같은 그림이 다른 곳(배경 등)에도 쓰이면 지우지 않음"""
    verdict: dict[int, bool] = {}
    with pymupdf.open(src) as doc:
        for page in doc:
            spans = [pymupdf.Rect(s['bbox']) for b in page.get_text('dict')['blocks'] if b['type'] == 0
                     for line in b['lines'] for s in line['spans'] if s['text'].strip()]
            for info in page.get_image_info(xrefs=True):
                image, xref = pymupdf.Rect(info['bbox']), info['xref']
                if xref <= 0:
                    continue
                # 페이지 폭 절반 이하, 글자를 감싸고 글자 높이 3배 이하인 그림만 (배경·띠 그림 제외)
                shadow = any(image.contains(r) and image.width < page.rect.width * 0.5 and image.height < r.height * 3
                             and image.get_area() < 6 * r.get_area() for r in spans)
                verdict[xref] = verdict.get(xref, True) and shadow
        removed = [xref for xref, shadow in verdict.items() if shadow]
        for xref in removed:
            for page in doc:
                if any(info['xref'] == xref for info in page.get_image_info(xrefs=True)):
                    page.delete_image(xref)
                    break  # delete_image는 문서 전체에서 그 그림을 지움
        doc.save(dst)
    return len(removed)


def glossary_csv(course: str | None, lang: str, path: Path) -> Path | None:
    """우리 용어집 → BabelDOC 용어집 CSV (source,target,tgt_lng)"""
    rows = []
    for term in g.load(service.glossary_paths(course)):
        rule = term.effective_rule(lang)
        target = term.translations.get(lang, '')
        if rule == 'keep':
            rows.append((term.term, term.term))
        elif rule == 'fixed':
            rows.append((term.term, target))
        elif target:
            rows.append((term.term, f'{target} ({term.term})'))
    if not rows:
        return None
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['source', 'target', 'tgt_lng'])
        writer.writerows((source, target, lang) for source, target in rows)
    return path


class LayoutPdf:
    def __init__(self, binary: str | None, run: Callable = subprocess.run, now: Callable[[], float] = time.time) -> None:
        self.binary, self.run, self.now = binary, run, now
        self.pool = ThreadPoolExecutor(max_workers=1)  # 한 번에 하나씩 (CPU·메모리 2GB+·토큰 보호)
        self.running: set[str] = set()
        self.lock = Lock()

    def result_path(self, doc_id: str) -> Path:
        return layout_dir() / f'{doc_id}.{VERSION}.pdf'

    def error_path(self, doc_id: str) -> Path:
        return layout_dir() / f'{doc_id}.{VERSION}.error'

    def request(self, doc_id: str) -> tuple[str, Path | str | None]:
        """('ready', 경로) | ('processing', None) | ('failed', 메시지) | ('missing', None) | ('disabled', None)"""
        if (path := self.result_path(doc_id)).exists():
            return 'ready', path
        if not self.binary:
            return 'disabled', None
        if service.get_document(doc_id) is None or not (originals_dir() / f'{doc_id}.pdf').exists():
            return 'missing', None
        with self.lock:
            if doc_id in self.running:
                return 'processing', None
            error = self.error_path(doc_id)
            if error.exists() and self.now() - float(error.read_text(encoding='utf-8') or 0) < RETRY_SECONDS:
                return 'failed', 'PDF를 만들지 못함. 잠시 후 다시 시도하세요.'
            self.running.add(doc_id)
        self.pool.submit(self._job, doc_id)
        return 'processing', None

    def _job(self, doc_id: str) -> None:
        try:
            self._make(doc_id)
            self.error_path(doc_id).unlink(missing_ok=True)
        except Exception as e:  # 실패는 파일로 남겨 RETRY_SECONDS 동안 재시도하지 않음
            log.warning('layout pdf failed for %s: %s', doc_id[:8], type(e).__name__)
            layout_dir().mkdir(parents=True, exist_ok=True)
            self.error_path(doc_id).write_text(str(self.now()), encoding='utf-8')
        finally:
            with self.lock:
                self.running.discard(doc_id)

    def _make(self, doc_id: str) -> None:
        doc = service.get_document(doc_id)
        layout_dir().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=layout_dir()) as tmp:
            work = Path(tmp)
            source = work / 'input.pdf'
            remove_title_shadows(originals_dir() / f'{doc_id}.pdf', source)
            key = os.environ.get('LLM_API_KEY', '')
            command = [
                self.binary, str(source), '--output', str(work),
                '--lang-in', 'ko', '--lang-out', doc['lang'], '--no-dual',
                '--watermark-output-mode', 'no_watermark', '--primary-font-family', 'sans-serif',
                '--split-short-lines', '--min-text-length', '1',
                '--openai', '--openai-model', os.getenv('PDF2ZH_MODEL') or os.getenv('LLM_MODEL', 'gpt-4o-mini'),
                '--openai-api-key', key, '--openai-temperature', '0', '--custom-system-prompt', PROMPT,
            ]
            if base := os.getenv('LLM_BASE_URL'):
                command += ['--openai-base-url', base]
            if csv_path := glossary_csv(doc.get('course'), doc['lang'], work / 'glossary.csv'):
                command += ['--glossaries', str(csv_path)]
            result = self.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=TIMEOUT_SECONDS, cwd=work)
            outputs = sorted(work.glob('*.mono.pdf'))
            if result.returncode != 0 or not outputs:
                tail = (result.stderr or result.stdout or '')[-800:]
                log.warning('pdf2zh exit %s: %s', result.returncode, tail.replace(key, '***') if key else tail)
                raise LayoutError('pdf2zh 실패')
            outputs[0].replace(self.result_path(doc_id))


@lru_cache(maxsize=1)
def get_layout() -> LayoutPdf:
    return LayoutPdf(os.getenv('PDF2ZH_BIN') or None)
