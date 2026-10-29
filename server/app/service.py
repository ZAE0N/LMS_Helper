import os
from pathlib import Path

from . import glossary as g
from .llm import LLM
from .pdf import extract_pages
from .translate import LANGS, Cache, glossary_version, translate_page

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_DIR = Path(os.getenv("GLOSSARY_DIR", ROOT / "glossary"))
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))


def glossary_paths(course: str | None) -> list[Path]:
    paths = [GLOSSARY_DIR / "common.json"]
    if course:
        p = GLOSSARY_DIR / f"{course}.json"
        if not p.exists():
            raise FileNotFoundError(f"용어집 없음: {p.name}")
        paths.append(p)
    return paths


def translate_pdf(data: bytes, lang: str, course: str | None, llm: LLM) -> dict:
    if lang not in LANGS:
        raise ValueError(f"지원하지 않는 언어: {lang}")
    paths = glossary_paths(course)
    terms = g.load(paths)
    gver = glossary_version(paths)
    docs = Cache(DATA_DIR / "docs")
    pages_cache = Cache(DATA_DIR / "pages")

    extracted = extract_pages(data)
    # 바이트가 아니라 내용 기준 (같은 교안을 다시 내보내도 재사용)
    doc_id = Cache.key(*(p["text"] for p in extracted), lang, gver)
    if (hit := docs.get(doc_id)) is not None:
        return hit

    pages = []
    for p in extracted:
        k = Cache.key(p["text"], lang, gver)
        if (r := pages_cache.get(k)) is None:
            r = translate_page(p["text"], lang, terms, llm)
            pages_cache.put(k, r)
        pages.append({"page": p["page"], "original": p["text"], **r})

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


def get_document(doc_id: str) -> dict | None:
    return Cache(DATA_DIR / "docs").get(doc_id)
