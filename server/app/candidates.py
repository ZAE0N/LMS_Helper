"""교안에서 용어 후보 추출 → 팀원 검수용 JSON 생성"""
import json
import re
from collections import Counter

from .llm import LLM

LATIN = re.compile(r"\b(?:[A-Z]{2,}[a-z]*|[A-Za-z][a-z]+(?:[A-Z][a-z]+)+|[A-Za-z]+(?:[ -][A-Z][A-Za-z]+)+)\b", re.ASCII)
STOP = {"PDF", "PPT", "OK", "THE", "AND"}

PROMPT = """아래는 대학 강의 교안 텍스트다. 외국인 유학생용 번역 용어집 후보를 골라라.
- keep: 영어 전문용어·약어·코드명 (번역하면 안 됨)
- bilingual: 시험·수업에서 한국어로 알아야 하는 핵심 개념 (번역어 + 한국어 병기)
- fixed: 학사/과제 관련 반복 용어 (항상 같은 번역어)
JSON 배열만 출력: [{{"term": "...", "rule": "keep|bilingual|fixed", "reason": "..."}}]
최대 40개.

{text}"""


def regex_candidates(pages: list[dict], top: int = 60) -> list[dict]:
    c = Counter(m.group(0).strip() for p in pages for m in LATIN.finditer(p["text"]))
    return [
        {"term": t, "rule": "keep", "count": n, "reason": "영문 용어 자동 추출"}
        for t, n in c.most_common(top)
        if t.upper() not in STOP
    ]


def llm_candidates(pages: list[dict], llm: LLM, max_chars: int = 12000) -> list[dict]:
    text = "\n".join(f"[p{p['page']}] {p['text']}" for p in pages)[:max_chars]
    raw = llm.chat("You output only valid JSON.", PROMPT.format(text=text))
    m = re.search(r"\[.*\]", raw, re.S)
    return json.loads(m.group(0)) if m else []


def merge(*lists: list[dict]) -> list[dict]:
    seen, out = set(), []
    for lst in lists:
        for c in lst:
            if c["term"].lower() not in seen:
                seen.add(c["term"].lower())
                out.append({"term": c["term"], "rule": c["rule"], "translations": {}, "note": c.get("reason", "")})
    return out
