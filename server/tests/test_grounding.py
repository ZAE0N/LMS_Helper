import pytest

from app.grounding import contains, locate

TEXT = '왜Hardhat + ethers.js 인가?\nRemix: 웹브라우저에서바로실습하기좋음, 큰프로젝트및자동화에는불편'


@pytest.mark.parametrize('quote', [
    'Remix: 웹브라우저에서 바로 실습하기 좋음',        # 띄어쓰기 추가
    '왜 Hardhat + ethers.js 인가?',                   # 글머리표 문자 무시
    '“Remix: 웹브라우저에서바로실습하기좋음”',          # 따옴표
    'REMIX: 웹브라우저에서바로실습하기좋음',            # 대소문자
    '큰 프로젝트 및 자동화에는 불편.',                  # 끝 문장부호
])
def test_layout_differences_are_ignored(quote):
    assert contains(quote, TEXT)


@pytest.mark.parametrize('quote', ['Invented quote', '블록체인은 탈중앙화되어 있다', '', '  ', 'Remix: 모바일에서 실습하기 좋음'])
def test_fabricated_text_is_rejected(quote):
    assert not contains(quote, TEXT)


def test_small_punctuation_change_in_long_quote_is_accepted():
    text = 'Hardhat runs tests automatically, and version control is convenient.'
    assert contains('Hardhat runs tests automatically and version control is convenient', text)
    assert not contains('Hardhat runs tests manually', text)


def test_locate_prefers_given_page_then_others():
    texts = [(1, 'alpha beta gamma delta'), (2, 'alpha beta gamma delta'), (3, 'other text here')]
    assert locate('alpha beta gamma', texts, 2) == 2
    assert locate('other text here', texts, 1) == 3
    assert locate('missing', texts, 1) is None
