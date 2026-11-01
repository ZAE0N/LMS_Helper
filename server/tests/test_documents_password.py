import re

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.layout_pdf import originals_dir
from app.pdf import unlock_pdf


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return re.sub(r"[가-힣]+", "Lecture", user.split("Text:\n", 1)[1])


def make_pdf(password: str | None = "1234") -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 60), "강의 내용", fontname="korea", fontsize=20)
        if password is None:
            return doc.tobytes()
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                           user_pw=password, owner_pw="x", permissions=0)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "DATA_DIR", tmp_path / "data")
    model = FakeLLM()
    factories = []

    def llm():
        factories.append(True)
        return model

    monkeypatch.setattr(main, "llm", llm)
    return TestClient(main.app), model, factories


def upload(client, data: bytes, password: str | None = None):
    form = {"lang": "ru"}
    if password is not None:
        form["password"] = password
    return client.post("/documents", files={"file": ("lecture.pdf", data, "application/pdf")}, data=form)


@pytest.mark.parametrize(("password", "detail"), [
    (None, "PDF_PASSWORD_REQUIRED"),
    ("", "PDF_PASSWORD_REQUIRED"),
    ("wrong-secret-7c91", "PDF_PASSWORD_WRONG"),
])
def test_missing_or_wrong_password_stops_before_llm_and_storage(setup, caplog, password, detail):
    client, model, factories = setup
    response = upload(client, make_pdf(), password)
    assert response.status_code == 401
    assert response.json() == {"detail": detail}
    assert not factories and not model.prompts
    assert not service.DATA_DIR.exists()
    assert "1234" not in caplog.text and "wrong-secret-7c91" not in caplog.text
    assert "wrong-secret-7c91" not in response.text


def test_correct_password_decrypts_every_downstream_pdf_and_saved_original(setup, monkeypatch, caplog):
    client, model, _ = setup
    source = make_pdf()
    checked = []

    def spy(name):
        original = getattr(service, name)

        def call(data, *args, **kwargs):
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                assert not doc.needs_pass and not doc.is_encrypted
                assert "강의 내용" in doc[0].get_text()
            checked.append(name)
            return original(data, *args, **kwargs)
        monkeypatch.setattr(service, name, call)

    for name in ("analyze_pdf", "figure_labels", "render_page"):
        spy(name)
    response = upload(client, source, "1234")
    assert response.status_code == 200
    result = response.json()
    page = result["pages"][0]
    assert page["page"] == 1 and page["original"] == "강의 내용"
    assert "Lecture" in page["translated"] and page["blocks"][0]["translated"]
    assert set(checked) == {"analyze_pdf", "figure_labels", "render_page"}
    for field in ("image", "background"):
        image = client.get(f"/documents/{result['doc_id']}/figures/{page[field]}")
        assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")
    with pymupdf.open(originals_dir() / f"{result['doc_id']}.pdf") as saved:
        assert not saved.needs_pass and not saved.is_encrypted
        assert "강의 내용" in saved[0].get_text()
    with pymupdf.open(stream=source, filetype="pdf") as encrypted:
        assert encrypted.needs_pass
    assert model.prompts and all("1234" not in str(prompt) for prompt in model.prompts)
    assert "1234" not in caplog.text and "password" not in response.text
    assert client.get(f"/documents/{result['doc_id']}").json() == result


def test_cached_translation_still_requires_valid_password(setup):
    client, model, factories = setup
    data = make_pdf()
    first = upload(client, data, "1234")
    assert first.status_code == 200
    calls = len(model.prompts), len(factories)
    assert upload(client, data).status_code == 401
    assert upload(client, data, "incorrect").status_code == 401
    assert calls == (len(model.prompts), len(factories))
    assert upload(client, data, "1234").json() == first.json()
    assert len(model.prompts) == calls[0]


@pytest.mark.parametrize(("pdf_password", "supplied"), [(None, None), (None, "unused"), ("", None), (" 1234 ", " 1234 ")])
def test_unprotected_owner_restricted_and_whitespace_password_pdfs(setup, pdf_password, supplied):
    client, _, _ = setup
    response = upload(client, make_pdf(pdf_password), supplied)
    assert response.status_code == 200 and len(response.json()["pages"]) == 1


def test_password_whitespace_is_not_stripped(setup):
    client, _, _ = setup
    response = upload(client, make_pdf(" 1234 "), "1234")
    assert response.status_code == 401 and response.json()["detail"] == "PDF_PASSWORD_WRONG"


def test_invalid_pdf_returns_safe_error_before_llm(setup, caplog):
    client, model, factories = setup
    password = "private-secret-6d31"
    response = upload(client, b"%PDF- broken", password)
    assert response.status_code == 400
    assert password not in response.text + caplog.text
    assert not factories and not model.prompts


def test_unprotected_pdf_bytes_are_preserved():
    data = make_pdf(None)
    assert unlock_pdf(data) is data
