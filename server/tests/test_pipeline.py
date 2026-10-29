import re

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import glossary as g
from app import main, service
from app import protect as pr
from app.translate import translate_text

CODE = 'String sql = "SELECT * FROM users WHERE id = ?";'
PAGE1 = f"""입력값 검증을 하지 않으면 SQL Injection 공격에 취약해진다.
이를 막으려면 PreparedStatement를 사용해야 한다.
{CODE}
과제는 금요일까지 제출한다."""
PAGE2 = "인증과 인가의 차이를 설명하라. 결석계는 학과 사무실에 낸다."


class FakeLLM:
    """규칙을 지키는 가짜 번역기. bad_first면 첫 응답에서 자리표시와 KEEP 용어를 빠뜨림"""

    def __init__(self, bad_first=False, always_bad=False):
        self.calls = 0
        self.bad_first, self.always_bad = bad_first, always_bad

    def chat(self, system, user):
        self.calls += 1
        text = user.split("Text:\n", 1)[1]
        toks = {}

        def tok(s):
            toks[f"@{len(toks)}@"] = s
            return f"@{len(toks) - 1}@"

        for line in user.split("Text:\n", 1)[0].splitlines():
            if m := re.match(r'- FIXED: "(.+?)" → 반드시 "(.+?)"', line):
                text = text.replace(m[1], tok(m[2]))
            elif m := re.match(r'- BILINGUAL: "(.+?)"(?:.*참고 번역: "(.+?)")?', line):
                text = text.replace(m[1], tok(f"{m[2] or 'T'} ({m[1]})"))
        text = re.sub(r"[가-힣]+", "tr", text)
        for k, v in toks.items():
            text = text.replace(k, v)
        if self.always_bad or (self.bad_first and self.calls == 1):
            text = re.sub(r"⟦C\d+⟧", "", text).replace("SQL Injection", "SQL insertion")
        return text


def make_pdf(*pages):
    doc = pymupdf.open()
    for t in pages:
        p = doc.new_page()
        p.insert_text((40, 60), t, fontname="korea", fontsize=10)
    return doc.tobytes()


@pytest.fixture
def terms():
    return g.load(service.glossary_paths("secure-coding"))


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)


def test_protect_roundtrip():
    masked, saved = pr.protect(PAGE1)
    assert CODE in saved
    assert "PreparedStatement" not in masked or "PreparedStatement" in "".join(saved)
    assert pr.restore(masked, saved) == PAGE1


def test_protect_does_not_swallow_particle():
    masked, saved = pr.protect("PreparedStatement를 사용하고 get_user()로 조회")
    assert saved == ["PreparedStatement", "get_user()"]
    assert "를 사용" in masked and "로 조회" in masked


def test_korean_term_boundaries(terms):
    found = lambda s: {t.term for t in g.find(s, terms)}
    assert "인가" not in found("이것은 무엇인가")
    assert "인가" in found("인가를 확인한다")
    assert found("출석인정 신청") >= {"출석인정"} and "출석" not in found("출석인정 신청")


def test_translate_pdf_keeps_code_and_terms(terms):
    llm = FakeLLM()
    r = service.translate_pdf(make_pdf(PAGE1, PAGE2), "en", "secure-coding", llm)
    p1, p2 = r["pages"]
    assert r["issue_pages"] == []
    assert CODE in p1["translated"]
    assert "SQL Injection" in p1["translated"] and "PreparedStatement" in p1["translated"]
    assert "input validation (입력값 검증)" in p1["translated"]
    assert "assignment" in p1["translated"]
    assert "authentication (인증)" in p2["translated"] and "authorization (인가)" in p2["translated"]
    assert "absence report (결석계)" in p2["translated"]

    before = llm.calls
    assert service.translate_pdf(make_pdf(PAGE1, PAGE2), "en", "secure-coding", llm)["doc_id"] == r["doc_id"]
    assert llm.calls == before  # 캐시


def test_retry_fixes_issues(terms):
    llm = FakeLLM(bad_first=True)
    r = translate_text(PAGE1, "en", terms, llm)
    assert llm.calls == 2 and r["issues"] == []


def test_issues_reported_when_still_broken(terms):
    r = translate_text(PAGE1, "en", terms, FakeLLM(always_bad=True))
    assert any("자리표시" in i for i in r["issues"])
    assert any("SQL Injection" in i for i in r["issues"])


def test_missing_fixed_translation_falls_back_to_bilingual():
    r = service.translate_pdf(make_pdf("과제는 금요일까지 제출한다."), "ru", None, FakeLLM())
    assert "과제" in r["needs_review"]
    assert "(과제)" in r["pages"][0]["translated"]


def test_api(monkeypatch):
    monkeypatch.setattr(main, "llm", lambda: FakeLLM())
    c = TestClient(main.app)
    pdf = make_pdf(PAGE1)
    r = c.post("/documents", files={"file": ("a.pdf", pdf)}, data={"lang": "en", "course": "secure-coding"})
    assert r.status_code == 200
    assert c.get(f"/documents/{r.json()['doc_id']}").status_code == 200
    assert c.post("/documents", files={"file": ("a.txt", b"hello")}, data={"lang": "en"}).status_code == 400
    assert c.post("/documents", files={"file": ("a.pdf", pdf)}, data={"lang": "xx"}).status_code == 400
    assert c.post("/documents", files={"file": ("a.pdf", pdf)}, data={"lang": "en", "course": "../x"}).status_code == 400
    cands = c.post("/glossary/candidates", files={"file": ("a.pdf", pdf)}, data={"use_llm": "false"}).json()["terms"]
    assert any(t["term"] == "PreparedStatement" for t in cands)
