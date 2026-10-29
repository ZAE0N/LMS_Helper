import hashlib
import json
import re
from pathlib import Path

from . import glossary as g
from . import protect as pr
from .llm import LLM
from .pdf import split_chunks

LANGS = {
    "en": "English",
    "ru": "Russian",

    # 하단 목록은 추가 가능 부분. MVP에서는 영어, 러시아어만
    # "ja": "Japanese",
    # "mn": "Mongolian",
    # "vi": "Vietnamese",
    # "uz": "Uzbek",
    # "ne": "Nepali",
    # "zh": "Simplified Chinese"
}
HANGUL = re.compile(r"[가-힣]")
UNTRANSLATED_RATIO = 0.15

SYSTEM = """You translate Korean university course materials into {lang}.
Rules:
1. Keep every placeholder like ⟦C1⟧ exactly once, unchanged, in place.
2. Follow the glossary exactly (KEEP / FIXED / BILINGUAL).
3. Keep line breaks and list structure. Do not add explanations.
4. Output only the translation."""

USER = """Glossary:
{glossary}

Text:
{text}"""

RETRY = """Your previous translation broke these rules:
{issues}

Translate again following all rules.

Glossary:
{glossary}

Text:
{text}"""


def _untranslated(out: str, terms: list[g.Term]) -> bool:
    for t in terms:
        out = out.replace(f"({t.term})", "")
    letters = [c for c in out if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if HANGUL.match(c)) / len(letters) > UNTRANSLATED_RATIO


def translate_text(text: str, lang: str, terms_all: list[g.Term], llm: LLM) -> dict:
    if not HANGUL.search(text):
        return {"translated": text, "terms": [], "issues": []}

    masked, saved = pr.protect(text)
    terms = g.find(masked, terms_all)
    block = g.prompt_block(terms, lang) or "(none)"
    system = SYSTEM.format(lang=LANGS[lang])

    def run(prompt: str) -> tuple[str, list[str]]:
        out = llm.chat(system, prompt)
        issues = pr.check(out, saved) + g.verify(out, terms, lang)
        if _untranslated(out, terms):
            issues.append("번역되지 않은 한국어가 많이 남음")
        return out, issues

    out, issues = run(USER.format(glossary=block, text=masked))
    if issues:
        out, issues = run(RETRY.format(issues="\n".join(f"- {i}" for i in issues), glossary=block, text=masked))

    return {"translated": pr.restore(out, saved), "terms": [t.term for t in terms], "issues": issues}


def translate_page(text: str, lang: str, terms_all: list[g.Term], llm: LLM) -> dict:
    parts = [translate_text(c, lang, terms_all, llm) for c in split_chunks(text)] or [
        {"translated": "", "terms": [], "issues": []}
    ]
    return {
        "translated": "\n\n".join(p["translated"] for p in parts),
        "terms": sorted({t for p in parts for t in p["terms"]}),
        "issues": [i for p in parts for i in p["issues"]],
    }


class Cache:
    """같은 텍스트·언어·용어집이면 재사용 (교안은 여러 학생이 공유)"""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    def get(self, k: str):
        p = self.root / f"{k}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, k: str, v) -> None:
        (self.root / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")


def glossary_version(paths: list[Path]) -> str:
    return Cache.key(*(Path(p).read_text(encoding="utf-8") for p in paths))
