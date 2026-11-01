"""LLM이 인용한 문구가 교안에 실제로 있는지 확인.

PDF에서 뽑은 원문은 띄어쓰기가 빠지거나(한글 PDF) 글머리표가 사용자 정의 문자(U+E000~F8FF)로
들어오는 경우가 많아, 공백·기호·따옴표 차이는 무시하고 비교한다.
"""
import re
import unicodedata
from difflib import SequenceMatcher

_DROP = re.compile(r'[\s-□•·‧・]+')
_UNIFY = str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '‥': '..', '…': '...'})
_EDGE = '.,;:!?"\'()[]{}-'
MIN_FUZZY = 15  # 이보다 짧은 인용은 정확히 일치해야 함
FUZZY_RATIO = 0.9


def normalize(text: str) -> str:
    return _DROP.sub('', unicodedata.normalize('NFKC', text).translate(_UNIFY).casefold()).strip(_EDGE)


def contains(quote: str, text: str) -> bool:
    q, t = normalize(quote), normalize(text)
    if not q:
        return False
    if q in t:
        return True
    if len(q) < MIN_FUZZY or not t:
        return False
    # 가장 길게 겹치는 위치 주변에서 거의 같은지 (따옴표·쉼표 등 몇 글자 차이 허용)
    a, b, size = SequenceMatcher(None, q, t, autojunk=False).find_longest_match(0, len(q), 0, len(t))
    if not size:
        return False
    start = max(0, b - a)
    window = t[start:start + len(q) + len(q) // 10]
    return SequenceMatcher(None, q, window, autojunk=False).ratio() >= FUZZY_RATIO


def locate(quote: str, texts: list[tuple[int, str]], preferred: object = None) -> int | None:
    """인용문이 있는 페이지 번호. 모델이 말한 페이지를 먼저 보고, 없으면 다른 페이지"""
    ordered = sorted(texts, key=lambda pt: pt[0] != preferred)
    return next((page for page, text in ordered if contains(quote, text)), None)
