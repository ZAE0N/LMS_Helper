import copy
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from . import glossary as g
from .figure_ocr import figure_labels
from .llm import LLM
from .structure import MARK, analyze_pdf, apply_marked, marked_text, render_page
from .translate import LANGS, Cache, glossary_version, translate_page

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_DIR = Path(os.getenv("GLOSSARY_DIR", ROOT / "glossary"))
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
STRUCTURE_VERSION = "slide-v3"  # 페이지 형식(blocks 등)을 바꾸면 올려서 교안을 새로 만들게 함
_DOCUMENT_LOCKS = [Lock() for _ in range(64)]
_PAGE_LOCKS = [Lock() for _ in range(64)]


def glossary_paths(course: str | None) -> list[Path]:
    paths = [GLOSSARY_DIR / "common.json"]
    if course:
        p = GLOSSARY_DIR / f"{course}.json"
        if not p.exists():
            raise FileNotFoundError(f"용어집 없음: {p.name}")
        paths.append(p)
    return paths


def translate_pdf(data: bytes, lang: str, course: str | None, llm: LLM, *, workers: int | None = None) -> dict:
    if lang not in LANGS:
        raise ValueError(f"지원하지 않는 언어: {lang}")
    workers = int(os.getenv('TRANSLATION_WORKERS', '3')) if workers is None else workers
    if not 1 <= workers <= 5:
        raise ValueError('TRANSLATION_WORKERS는 1~5만 가능')
    paths = glossary_paths(course)
    terms = g.load(paths)
    gver = glossary_version(paths)
    docs = Cache(DATA_DIR / "docs")
    pages_cache = Cache(DATA_DIR / "pages")

    extracted = analyze_pdf(data)
    # 바이트가 아니라 내용 기준 (같은 교안을 다시 내보내도 재사용)
    doc_id = Cache.key(*(p["text"] for p in extracted), lang, gver, STRUCTURE_VERSION)

    def process_page(p: dict) -> dict:
        blocks = copy.deepcopy(p["blocks"])
        # 통째로 붙인 그림 속 한글(OCR)도 같은 자리에 번역
        blocks += figure_labels(data, p["page"], p.get("figures") or [], [b["rect"] for b in blocks if b["type"] == "label"])
        source = marked_text(blocks) or p["text"]
        k = Cache.key(source, lang, gver)
        with _PAGE_LOCKS[int(k[:8], 16) % len(_PAGE_LOCKS)]:
            if (r := pages_cache.get(k)) is None:
                r = translate_page(source, lang, terms, llm)
                pages_cache.put(k, r)
        page = {"page": p["page"], "original": p["text"], **r, "size": p["size"]}
        # 덩어리별 번역이 어긋나면 블록의 translated는 없음 → 확장이 원본 페이지 + 페이지 전체 번역으로 보여 줌
        if source == p["text"] or not apply_marked(r["translated"], blocks):
            for b in blocks:
                b.pop("translated", None)
        page["translated"] = MARK.sub("", r["translated"]).replace("\n\n", "\n").strip()
        save_pages(doc_id, data, page, blocks)
        for b in blocks:
            b.pop("lines", None)
            b.pop("fill", None)
        page["blocks"] = blocks
        return page

    with _DOCUMENT_LOCKS[int(doc_id[:8], 16) % len(_DOCUMENT_LOCKS)]:
        if (hit := docs.get(doc_id)) is not None:
            return hit
        if workers == 1:
            pages = [process_page(p) for p in extracted]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending = [pool.submit(process_page, p) for p in extracted]
                try:
                    completed = {}
                    for future in as_completed(pending):
                        page = future.result()
                        completed[page['page']] = page
                    pages = [completed[p['page']] for p in extracted]
                except Exception:
                    for future in pending:
                        future.cancel()
                    raise
        result = {
            "doc_id": doc_id,
            "lang": lang,
            "course": course,
            "needs_review": g.needs_review(terms, lang),
            "issue_pages": [p["page"] for p in pages if p["issues"]],
            "pages": pages,
        }
        docs.put(doc_id, result)
        return result


def figures_dir(doc_id: str) -> Path:
    return DATA_DIR / "figures" / doc_id


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def save_pages(doc_id: str, data: bytes, page: dict, blocks: list[dict]) -> None:
    """원본 페이지 그림(image)과 번역할 글자를 지운 배경(background) 저장. 확장은 배경 위 같은 자리에 번역문을 올림"""
    folder = figures_dir(doc_id)
    n = page["page"]
    changed = [b for b in blocks if b.get("translated") not in (None, b["text"])]
    erase = [b["lines"] for b in changed if not b.get("ocr")]
    fills = [(line, b["fill"]) for b in changed if b.get("ocr") for line in b["lines"]]
    page["image"] = f"{n}.jpg"
    if erase or fills:
        page["background"] = f"{n}-bg.jpg"
    if (folder / page["image"]).exists() and ("background" not in page or (folder / page["background"]).exists()):
        return
    original, background = render_page(data, n, erase, fills)
    _write(folder / page["image"], original)
    if background is not None:
        _write(folder / page["background"], background)


def get_document(doc_id: str) -> dict | None:
    return Cache(DATA_DIR / "docs").get(doc_id)
