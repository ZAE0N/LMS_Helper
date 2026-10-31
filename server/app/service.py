import copy
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from . import glossary as g
from .llm import LLM
from .figure_vision import read_figure
from .structure import MARK, analyze_pdf, apply_marked, marked_text, render_figure
from .translate import LANGS, Cache, glossary_version, translate_page

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_DIR = Path(os.getenv("GLOSSARY_DIR", ROOT / "glossary"))
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
STRUCTURE_VERSION = "blocks-v2"  # 교재 보기 형식을 바꾸면 올려서 교안을 새로 만들게 함
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
        blocks = copy.deepcopy(p.get("blocks"))
        source = (marked_text(blocks) if blocks else "") or p["text"]
        k = Cache.key(source, lang, gver)
        with _PAGE_LOCKS[int(k[:8], 16) % len(_PAGE_LOCKS)]:
            if (r := pages_cache.get(k)) is None:
                r = translate_page(source, lang, terms, llm)
                pages_cache.put(k, r)
        page = {"page": p["page"], "original": p["text"], **r}
        if blocks is not None:
            # 덩어리별 번역이 어긋나면 블록의 translated는 없음 → 확장이 페이지 전체 번역으로 보여 줌
            if source == p["text"] or not apply_marked(r["translated"], blocks):
                for b in blocks:
                    b.pop("translated", None)
            page["translated"] = MARK.sub("", r["translated"]).replace("\n\n", "\n").strip()
            save_figures(doc_id, data, p["page"], blocks, lang, llm)
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


def save_figures(doc_id: str, data: bytes, page: int, blocks: list[dict], lang: str, llm: LLM) -> None:
    """그림 영역을 PNG로 저장(image). 번역본(translated_image): 도형 그림은 글자를 번역해 다시 그리고,
    통째로 붙인 그림은 GPT가 다시 그린 SVG 또는 그림 속 글자 번역 목록(legend)"""
    folder = figures_dir(doc_id)
    for k, b in enumerate((x for x in blocks if x["type"] == "image"), 1):
        base = f"{page}-{k}"
        b["image"] = f"{base}.png"
        if not (folder / b["image"]).exists():
            _write(folder / b["image"], render_figure(data, page, b["rect"]))
        if any(label.get("translated") not in (None, label["text"]) for label in b["labels"]):
            b["translated_image"] = f"{base}.tr.png"
            if not (folder / b["translated_image"]).exists():
                _write(folder / b["translated_image"], render_figure(data, page, b["rect"], b["labels"]))
        elif b.get("raster") and hasattr(llm, "vision"):
            png = (folder / b["image"]).read_bytes()
            vision = Cache(DATA_DIR / "vision")
            key = Cache.key(hashlib.sha256(png).hexdigest(), lang, "figure-v1")
            if (result := vision.get(key)) is None:
                x0, y0, x1, y1 = b["rect"]
                result = read_figure(llm, png, LANGS[lang], round(x1 - x0), round(y1 - y0))
                vision.put(key, result)
            if result["svg"]:
                b["translated_image"] = f"{base}.tr.svg"
                _write(folder / b["translated_image"], result["svg"].encode("utf-8"))
            b["legend"] = result["labels"]


def get_document(doc_id: str) -> dict | None:
    return Cache(DATA_DIR / "docs").get(doc_id)
