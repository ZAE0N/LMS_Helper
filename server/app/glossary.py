import json
import re
from dataclasses import dataclass, field
from pathlib import Path

RULES = ("keep", "fixed", "bilingual")
MATCHES = ("word", "substring")
HANGUL = re.compile(r"[가-힣]")


@dataclass
class Term:
    term: str
    rule: str
    translations: dict[str, str] = field(default_factory=dict)
    note: str = ""
    match: str = "word"

    def effective_rule(self, lang: str) -> str:
        # 고정 번역어가 아직 없으면 병기로 처리하고 검수 대상으로 남김
        if self.rule == "fixed" and not self.translations.get(lang):
            return "bilingual"
        return self.rule


def load(paths: list[Path]) -> list[Term]:
    terms: dict[str, Term] = {}
    for p in paths:
        for t in json.loads(Path(p).read_text(encoding="utf-8"))["terms"]:
            if t["rule"] not in RULES:
                raise ValueError(f"{p}: '{t['term']}' 규칙 오류: {t['rule']}")
            if t.get('match', 'word') not in MATCHES:
                raise ValueError(f"{p}: '{t['term']}' 일치 방식 오류")
            terms[t["term"]] = Term(**t)  # 뒤 파일(과목 용어집)이 공통 용어집을 덮어씀
    return sorted(terms.values(), key=lambda t: -len(t.term))


PARTICLE = r"(?:은|는|이|가|을|를|의|에|에서|에게|도|로|으로|와|과|만|까지|부터|이다|입니다)"


def _pattern(term: str, match: str = "word") -> re.Pattern:
    if match == 'substring':
        return re.compile(re.escape(term))
    if HANGUL.search(term):
        # 단어 단독 또는 조사만 허용 ("인가"가 "무엇인가"에, "출석"이 "출석인정"에 걸리지 않게)
        return re.compile(rf"(?<![가-힣]){re.escape(term)}(?=$|[^가-힣]|{PARTICLE}(?![가-힣]))")
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE | re.ASCII)


def find(text: str, terms: list[Term]) -> list[Term]:
    return [t for t in terms if _pattern(t.term, t.match).search(text)]


def prompt_block(terms: list[Term], lang: str) -> str:
    lines = []
    for t in terms:
        r = t.effective_rule(lang)
        if r == "keep":
            lines.append(f'- KEEP: "{t.term}" → 그대로 둘 것')
        elif r == "fixed":
            lines.append(f'- FIXED: "{t.term}" → 반드시 "{t.translations[lang]}"')
        else:
            hint = f' (참고 번역: "{t.translations[lang]}")' if t.translations.get(lang) else ""
            lines.append(f'- BILINGUAL: "{t.term}" → 번역어 뒤에 "({t.term})" 병기{hint}')
    return "\n".join(lines)


def verify(output: str, terms: list[Term], lang: str) -> list[str]:
    issues = []
    for t in terms:
        r = t.effective_rule(lang)
        if r == "keep" and not _pattern(t.term, t.match).search(output):
            issues.append(f'KEEP 용어 누락: "{t.term}"')
        elif r == "fixed" and t.translations[lang] not in output:
            issues.append(f'FIXED 번역어 미사용: "{t.term}" → "{t.translations[lang]}"')
        elif r == "bilingual" and f"({t.term})" not in output:
            issues.append(f'BILINGUAL 원문 병기 누락: "({t.term})"')
    return issues


def needs_review(terms: list[Term], lang: str) -> list[str]:
    return [t.term for t in terms if t.rule == "fixed" and not t.translations.get(lang)]
