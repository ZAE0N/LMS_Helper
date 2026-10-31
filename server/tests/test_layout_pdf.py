import csv
from pathlib import Path
from subprocess import CompletedProcess

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.layout_pdf import RETRY_SECONDS, LayoutPdf, glossary_csv, remove_title_shadows, save_original
from app.translate import Cache

DOC_ID = 'b' * 64


def slide(path: Path) -> Path:
    """제목 글자 + 제목을 감싸는 그림자 그림 + 페이지 폭의 띠 그림"""
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    def image(color):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 10), False)
        pix.set_rect(pix.irect, color)
        return pix
    page.insert_image(pymupdf.Rect(0, 0, 720, 70), pixmap=image((200, 200, 230)), keep_proportion=False)   # 띠 (남아야 함)
    page.insert_image(pymupdf.Rect(270, 10, 470, 70), pixmap=image((150, 150, 150)), keep_proportion=False)  # 제목 그림자
    page.insert_text((290, 50), '강의 내용', fontname='korea', fontsize=30)
    page.insert_text((60, 150), '본문 글자', fontname='korea', fontsize=18)
    doc.save(path)
    return path


class FakeRun:
    def __init__(self, code=0, write=True):
        self.code, self.write, self.calls = code, write, []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if self.write:
            out = Path(command[command.index('--output') + 1])
            (out / 'input.no_watermark.ru.mono.pdf').write_bytes(b'%PDF-1.7 translated')
        return CompletedProcess(command, self.code, stdout='', stderr='boom sk-secret')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('LLM_API_KEY', 'sk-secret')
    monkeypatch.setenv('LLM_MODEL', 'gpt-4o-mini')
    Cache(tmp_path / 'docs').put(DOC_ID, {'doc_id': DOC_ID, 'lang': 'ru', 'course': 'secure-coding', 'pages': []})
    save_original(DOC_ID, slide(tmp_path / 'src.pdf').read_bytes())
    clock = [1_000_000.0]
    fake = FakeRun()
    layout = LayoutPdf('pdf2zh_next', run=fake, now=lambda: clock[0])
    monkeypatch.setattr(main, 'get_layout', lambda: layout)
    return TestClient(main.app), layout, fake, clock


def wait(layout):
    layout.pool.submit(lambda: None).result(timeout=10)  # 작업자는 하나라서 앞의 작업이 끝날 때까지 기다림


def test_title_shadow_removed_but_banner_and_text_kept(tmp_path):
    src = slide(tmp_path / 'a.pdf')
    assert remove_title_shadows(src, tmp_path / 'b.pdf') == 1
    with pymupdf.open(tmp_path / 'b.pdf') as doc:
        page = doc[0]
        boxes = [pymupdf.Rect(i['bbox']) for i in page.get_image_info() if not pymupdf.Rect(i['bbox']).is_empty]
        assert any(b.width == 720 for b in boxes)
        assert '강의 내용' in page.get_text()


def test_pdf_made_once_and_shared(setup):
    client, layout, fake, _ = setup
    first = client.get(f'/documents/{DOC_ID}/pdf')
    assert first.status_code == 202 and first.json() == {'status': 'processing'}
    wait(layout)
    ready = client.get(f'/documents/{DOC_ID}/pdf')
    assert ready.status_code == 200 and ready.headers['content-type'] == 'application/pdf'
    assert ready.content == b'%PDF-1.7 translated'
    assert client.get(f'/documents/{DOC_ID}/pdf').status_code == 200
    assert len(fake.calls) == 1
    command = fake.calls[0]
    for flag in ('--no-dual', '--split-short-lines', '--glossaries', '--custom-system-prompt'):
        assert flag in command
    assert command[command.index('--lang-out') + 1] == 'ru'
    assert command[command.index('--openai-temperature') + 1] == '0'
    assert command[command.index('--watermark-output-mode') + 1] == 'no_watermark'


def test_failure_is_not_retried_for_a_while_and_hides_key(setup, caplog):
    client, layout, fake, clock = setup
    fake.code, fake.write = 1, False
    client.get(f'/documents/{DOC_ID}/pdf')
    wait(layout)
    failed = client.get(f'/documents/{DOC_ID}/pdf')
    assert failed.status_code == 502 and 'detail' in failed.json()
    assert len(fake.calls) == 1
    assert 'sk-secret' not in caplog.text
    clock[0] += RETRY_SECONDS
    fake.code, fake.write = 0, True
    assert client.get(f'/documents/{DOC_ID}/pdf').status_code == 202
    wait(layout)
    assert client.get(f'/documents/{DOC_ID}/pdf').status_code == 200


def test_disabled_missing_and_invalid(setup, monkeypatch, tmp_path):
    client, layout, fake, _ = setup
    monkeypatch.setattr(main, 'get_layout', lambda: LayoutPdf(None))
    assert client.get(f'/documents/{DOC_ID}/pdf').status_code == 503
    monkeypatch.setattr(main, 'get_layout', lambda: layout)
    assert client.get(f'/documents/{"c" * 64}/pdf').status_code == 404
    assert client.get('/documents/not-an-id/pdf').status_code == 404
    assert fake.calls == []


def test_documents_upload_keeps_original_and_starts_pdf(setup, monkeypatch, tmp_path):
    client, layout, fake, _ = setup
    new_id = 'd' * 64
    def translated(data, lang, course, llm):
        Cache(service.DATA_DIR / 'docs').put(new_id, {'doc_id': new_id, 'lang': lang, 'course': None, 'pages': []})
        return {'doc_id': new_id, 'lang': lang, 'pages': []}
    monkeypatch.setattr(service, 'translate_pdf', translated)
    monkeypatch.setattr(main, 'llm', lambda: None)
    pdf = slide(tmp_path / 'up.pdf').read_bytes()
    response = client.post('/documents', files={'file': ('a.pdf', pdf, 'application/pdf')}, data={'lang': 'ru'})
    assert response.status_code == 200
    assert (service.DATA_DIR / 'originals' / f'{new_id}.pdf').read_bytes() == pdf
    wait(layout)
    assert len(fake.calls) == 1


def test_glossary_csv_follows_rules(tmp_path):
    path = glossary_csv('secure-coding', 'ru', tmp_path / 'g.csv')
    rows = {r['source']: r for r in csv.DictReader(path.open(encoding='utf-8'))}
    assert rows['XSS']['target'] == 'XSS'
    assert rows['인증']['target'] == 'аутентификация (인증)'
    assert all(r['tgt_lng'] == 'ru' for r in rows.values())
