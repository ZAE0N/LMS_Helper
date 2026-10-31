import re

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.figure_vision import safe_svg
from app.structure import analyze_pdf, apply_marked, marked_text


def slide_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((250, 60), '강의 내용', fontname='korea', fontsize=30)
    # 띄어쓰기 없이 낱말을 따로 배치한 슬라이드 (한글 PDF에서 흔함)
    page.insert_text((40, 130), '□', fontname='korea', fontsize=20)
    page.insert_text((70, 130), '블록체인', fontname='korea', fontsize=20)
    page.insert_text((160, 130), '프로그래밍', fontname='korea', fontsize=20)
    page.insert_text((90, 170), '□', fontname='korea', fontsize=18)
    page.insert_text((115, 170), '기본 지식', fontname='korea', fontsize=18)
    page.insert_text((70, 210), 'const abi = [1, 2];', fontname='cour', fontsize=12)
    page.insert_text((70, 226), 'console.log(abi);', fontname='cour', fontsize=12)
    for x in (100, 300, 500):  # 도형 묶음 = 다이어그램
        page.draw_rect(pymupdf.Rect(x, 280, x + 120, 380), color=(0, 0, 1), width=2)
    page.insert_text((110, 330), '이전 블록', fontname='korea', fontsize=10)
    page.insert_text((40, 520), '인하공업전문대학', fontname='korea', fontsize=10)  # 꼬리말
    data = doc.tobytes()
    doc.close()
    return data


def test_structure_headings_bullets_levels_code_and_figure():
    blocks = analyze_pdf(slide_pdf())[0]['blocks']
    kinds = [(b['type'], b.get('level'), b.get('text')) for b in blocks]
    assert kinds[0] == ('heading', 0, '강의 내용')
    assert ('bullet', 0, '블록체인 프로그래밍') in kinds          # 빠진 띄어쓰기 복원
    assert ('bullet', 1, '기본 지식') in kinds                    # 들여쓰기 단계
    assert ('code', 1, 'const abi = [1, 2];\nconsole.log(abi);') in kinds
    images = [b for b in blocks if b['type'] == 'image']
    assert len(images) == 1 and images[0]['labels'][0]['text'] == '이전 블록' and not images[0]['raster']
    texts = ' '.join(b.get('text') or '' for b in blocks)
    assert '이전 블록' not in texts and '인하공업전문대학' not in texts  # 도표 안 글자는 그림으로, 꼬리말 제외


def test_urls_and_numbers_are_not_sent_for_translation():
    blocks = [{'type': 'bullet', 'text': 'https://www.youtube.com/watch?v=XT22h'}, {'type': 'text', 'text': '15%'},
              {'type': 'bullet', 'text': '출석 : 15%'}]
    assert marked_text(blocks) == '⟦B0⟧ 출석 : 15%'
    assert apply_marked('⟦B0⟧ Attendance: 15%', blocks)
    assert [b['translated'] for b in blocks] == [blocks[0]['text'], '15%', 'Attendance: 15%']


def test_marked_round_trip_with_figure_labels_and_mismatch():
    blocks = [{'type': 'heading', 'text': '제목'}, {'type': 'code', 'text': 'x = 1'},
              {'type': 'image', 'labels': [{'text': '이전 블록'}, {'text': 'Nonce'}]}, {'type': 'bullet', 'text': '항목'}]
    assert marked_text(blocks) == '⟦B0⟧ 제목\n\n⟦B1⟧ 이전 블록\n\n⟦B2⟧ Nonce\n\n⟦B3⟧ 항목'
    assert apply_marked('⟦B0⟧ Title\n\n⟦B1⟧ Previous block\n\n⟦B2⟧ Nonce\n\n⟦B3⟧ Item', blocks)
    assert blocks[0]['translated'] == 'Title' and blocks[3]['translated'] == 'Item'
    assert blocks[2]['labels'][0]['translated'] == 'Previous block' and 'translated' not in blocks[1]
    assert not apply_marked('⟦B0⟧ Title Item', [{'type': 'heading', 'text': '제목'}, {'type': 'bullet', 'text': '항목'}])
    assert not apply_marked('⟦B0⟧ T\n\n⟦B1⟧ ', [{'type': 'heading', 'text': '제목'}, {'type': 'bullet', 'text': '항목'}])


def test_safe_svg_keeps_shapes_and_text_but_drops_active_content():
    svg = safe_svg('<svg viewBox="0 0 10 10" onload="x()"><rect width="5" height="5" fill="#00f"/>'
                   '<text x="1" y="9" style="background:url(http://x)">Ключ</text></svg>')
    assert '<rect' in svg and 'Ключ' in svg and 'onload' not in svg and 'http://x' not in svg
    assert safe_svg('<svg><script>alert(1)</script></svg>') is None
    assert safe_svg('<svg><image href="http://x/a.png"/></svg>') is None
    assert safe_svg('<!DOCTYPE svg [<!ENTITY a "b">]><svg>&a;</svg>') is None
    assert safe_svg('not svg') is None


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'llm', lambda: object())
    return TestClient(main.app)


def fake_translate(broken=False):
    def translate(text, lang, terms, llm):
        if broken:
            return {'translated': re.sub(r'⟦B\d+⟧', '', text), 'terms': [], 'issues': []}
        return {'translated': re.sub(r'(⟦B\d+⟧) (.+)', lambda m: f'{m[1]} RU:{m[2]}', text), 'terms': [], 'issues': []}
    return translate


def test_documents_have_translated_blocks_and_figures(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate())
    doc = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ru'}).json()
    page = doc['pages'][0]
    assert page['blocks'][0]['translated'] == 'RU:강의 내용'
    code = next(b for b in page['blocks'] if b['type'] == 'code')
    assert 'translated' not in code
    assert '⟦B' not in page['translated'] and 'RU:블록체인 프로그래밍' in page['translated']
    image = next(b for b in page['blocks'] if b['type'] == 'image')
    assert image['labels'][0]['translated'] == 'RU:이전 블록'
    for name in (image['image'], image['translated_image']):
        figure = client.get(f"/documents/{doc['doc_id']}/figures/{name}")
        assert figure.status_code == 200 and figure.content.startswith(b'\x89PNG')
    assert client.get(f"/documents/{doc['doc_id']}/figures/..%2Fx.png").status_code == 404
    assert client.get(f"/documents/{doc['doc_id']}/figures/9-9.png").status_code == 404


def test_broken_markers_fall_back_to_page_translation(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate(broken=True))
    page = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ru'}).json()['pages'][0]
    assert all('translated' not in b for b in page['blocks'] if b['type'] != 'image')
    assert page['translated'] and '⟦B' not in page['translated']


def photo_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((250, 60), '거래 구조', fontname='korea', fontsize=30)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pix.set_rect(pix.irect, (120, 180, 240))
    page.insert_image(pymupdf.Rect(200, 150, 520, 350), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


class VisionLLM:
    def __init__(self, output):
        self.output, self.calls = output, 0

    def vision(self, system, user, png):
        self.calls += 1
        assert png.startswith(b'\x89PNG') and 'Russian' in system
        return self.output


def test_raster_figure_is_redrawn_once_with_translated_labels(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate())
    fake = VisionLLM('{"svg": "<svg viewBox=\\"0 0 320 200\\"><rect width=\\"100\\" height=\\"50\\"/><text>Ключ A</text>'
                     '<script>x()</script></svg>", "labels": [{"source": "A 개인키", "translated": "Закрытый ключ A"}]}')
    monkeypatch.setattr(main, 'llm', lambda: fake)
    doc = client.post('/documents', files={'file': ('p.pdf', photo_pdf())}, data={'lang': 'ru'}).json()
    image = next(b for b in doc['pages'][0]['blocks'] if b['type'] == 'image')
    assert image['raster'] and 'translated_image' not in image  # 스크립트가 있는 SVG는 버림
    assert image['legend'] == [{'source': 'A 개인키', 'translated': 'Закрытый ключ A'}]
    fake.output = '{"svg": "<svg viewBox=\\"0 0 320 200\\"><text>Ключ A</text></svg>", "labels": []}'
    for f in (service.DATA_DIR / 'vision').glob('*.json'):
        f.unlink()
    for f in (service.DATA_DIR / 'docs').glob('*.json'):
        f.unlink()
    doc = client.post('/documents', files={'file': ('p.pdf', photo_pdf())}, data={'lang': 'ru'}).json()
    image = next(b for b in doc['pages'][0]['blocks'] if b['type'] == 'image')
    svg = client.get(f"/documents/{doc['doc_id']}/figures/{image['translated_image']}")
    assert svg.headers['content-type'].startswith('image/svg+xml') and 'Ключ A' in svg.text
    assert fake.calls == 2


def test_vision_failure_keeps_translation(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate())
    monkeypatch.setattr(main, 'llm', lambda: VisionLLM('not json'))
    response = client.post('/documents', files={'file': ('p.pdf', photo_pdf())}, data={'lang': 'ru'})
    image = next(b for b in response.json()['pages'][0]['blocks'] if b['type'] == 'image')
    assert response.status_code == 200 and image['legend'] == [] and 'translated_image' not in image
