import hashlib
import json
import re
import threading
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

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
# 화면·챗봇·복습 언어. 한국어(ko)는 교안을 번역하지 않고 원문 그대로 씀
LANG_NAMES = {"en": "English", "ru": "Russian", "ko": "Korean"}
Lang = Literal["en", "ru", "ko"]
HANGUL = re.compile(r"[가-힣]")
UNTRANSLATED_RATIO = 0.15

# 괄호 속 한국어 예시 (원문 → 번역). 영어(한국어) 순서도 넣어야 모델이 괄호 속 한국어를 병기로 보고 남기지 않음
EXAMPLES = {
    "en": ["정밀도(Precision) → Precision", "Precision(정밀도) → Precision",
           "FP(False Positive / 1종 오류) → FP (False Positive / Type I error)",
           "확정 손실(스킬라) → certain loss (Scylla)"],
    "ru": ["정밀도(Precision) → точность (Precision)", "Precision(정밀도) → Precision (точность)",
           "FP(False Positive / 1종 오류) → FP (False Positive / ошибка первого рода)",
           "확정 손실(스킬라) → гарантированные потери (Сцилла)"],
}

SYSTEM = """You translate Korean university course materials into {lang}.
Rules:
1. Keep every placeholder like ⟦C1⟧ exactly once, unchanged, in place.
2. Follow the glossary exactly (KEEP / FIXED / BILINGUAL). Only the glossary decides which Korean stays in Korean.
3. Keep line breaks and list structure. Do not add explanations.
   A paragraph that starts with a marker like ⟦B3⟧ must keep that marker unchanged at its start.
4. Proper nouns: use the official or widely used {lang} name when one exists
   (for example 인하공업전문대학 → Inha Technical College). Foreign or mythological names written in Korean letters
   (스킬라, 카리브디스) take their usual {lang} spelling. Keep Korean-only proper nouns that have no widely used {lang}
   name (people's names, Korean place, organization or service names) exactly as written in Korean.
5. Korean inside parentheses is ordinary text: translate it. It is not an annotation to keep.
   Keep English that is already in the text as it is, in either order. If the {lang} word is the same as the English
   next to it, write it only once. Examples:
{examples}
6. Before answering, check your whole output: Hangul may remain only for glossary KEEP or BILINGUAL terms and
   Korean-only proper nouns from rule 4. Translate any other Korean, including inside parentheses and next to English.
7. Output only the translation."""
PROMPT_VERSION = "p6"  # 위 규칙을 바꾸면 올려서 저장된 페이지 번역을 다시 만듦

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


HANGUL_WORD = re.compile(r"[가-힣]+(?:\s*[가-힣]+)*")


def _left_korean(out: str, terms: list[g.Term]) -> list[str]:
    """번역문에 남은 한국어 낱말 (용어집에서 그대로 두거나 병기하라는 용어는 제외)"""
    for t in terms:
        out = out.replace(t.term, "")
    return sorted(set(HANGUL_WORD.findall(out)))


def translate_text(text: str, lang: str, terms_all: list[g.Term], llm: LLM) -> dict:
    if not HANGUL.search(text):
        return {"translated": text, "terms": [], "issues": []}

    masked, saved = pr.protect(text)
    terms = g.find(masked, terms_all)
    block = g.prompt_block(terms, lang) or "(none)"
    system = SYSTEM.format(lang=LANGS[lang], examples="\n".join(f"   {x}" for x in EXAMPLES.get(lang, [])))

    def run(prompt: str) -> tuple[str, list[str]]:
        out = llm.chat(system, prompt)
        issues = pr.check(out, saved) + g.verify(out, terms, lang)
        if _untranslated(out, terms):
            issues.append("번역되지 않은 한국어가 많이 남음")
        return out, issues

    out, issues = run(USER.format(glossary=block, text=masked))
    # 남은 한국어 낱말은 고칠 기회를 한 번 더 줌 (한국에만 있는 이름이면 그대로 둬도 됨, 문제로 표시하지 않음)
    left = _left_korean(out, terms)
    if issues or left:
        notes = issues + ([f"Korean words left untranslated: {', '.join(left)}. Translate each of them, also when it is "
                           f"inside parentheses or next to English, unless it is a Korean-only name of a person, place "
                           f"or organization."] if left else [])
        out, issues = run(RETRY.format(issues="\n".join(f"- {i}" for i in notes), glossary=block, text=masked))

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


# 같은 캐시 파일은 한 번에 한 스레드만 읽고 씀 (Windows는 사용 중인 파일을 덮어쓰지 못함)
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


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
        with _lock(p):
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, k: str, v) -> None:
        target = self.root / f'{k}.json'
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(v, stream, ensure_ascii=False)
            with _lock(target):
                for attempt in range(5):
                    try:
                        temporary.replace(target)
                        break
                    except PermissionError:  # 백신 등 다른 프로그램이 잠깐 파일을 잡은 경우
                        if attempt == 4:
                            raise
                        time.sleep(0.05 * (attempt + 1))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def glossary_version(paths: list[Path]) -> str:
    return Cache.key(*(Path(p).read_text(encoding="utf-8") for p in paths))
