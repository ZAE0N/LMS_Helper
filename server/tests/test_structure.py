import re

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.structure import analyze_pdf, marked_text, split_marked


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
    assert [b['type'] for b in blocks].count('image') == 1
    texts = ' '.join(b.get('text') or '' for b in blocks)
    assert '이전 블록' not in texts and '인하공업전문대학' not in texts  # 도표 안 글자·꼬리말 제외


def test_urls_and_numbers_are_not_sent_for_translation():
    blocks = [{'type': 'bullet', 'text': 'https://www.youtube.com/watch?v=XT22h'}, {'type': 'text', 'text': '15%'},
              {'type': 'bullet', 'text': '출석 : 15%'}]
    assert marked_text(blocks) == '⟦B2⟧ 출석 : 15%'
    assert split_marked('⟦B2⟧ Attendance: 15%', blocks) == [blocks[0]['text'], '15%', 'Attendance: 15%']


def test_marked_round_trip_and_mismatch():
    blocks = [{'type': 'heading', 'text': '제목'}, {'type': 'code', 'text': 'x = 1'}, {'type': 'bullet', 'text': '항목'}]
    marked = marked_text(blocks)
    assert marked == '⟦B0⟧ 제목\n\n⟦B2⟧ 항목'
    assert split_marked('⟦B0⟧ Title\n\n⟦B2⟧ Item', blocks) == ['Title', None, 'Item']
    assert split_marked('⟦B0⟧ Title Item', blocks) is None          # 표시가 빠짐
    assert split_marked('⟦B0⟧ Title\n\n⟦B2⟧ ', blocks) is None      # 빈 번역


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
    heading = page['blocks'][0]
    assert heading['translated'] == 'RU:강의 내용'
    code = next(b for b in page['blocks'] if b['type'] == 'code')
    assert code['translated'] is None
    assert '⟦B' not in page['translated'] and 'RU:블록체인 프로그래밍' in page['translated']
    image = next(b for b in page['blocks'] if b['type'] == 'image')
    figure = client.get(f"/documents/{doc['doc_id']}/figures/{image['image']}")
    assert figure.status_code == 200 and figure.content.startswith(b'\x89PNG')
    assert client.get(f"/documents/{doc['doc_id']}/figures/..%2Fx.png").status_code == 404
    assert client.get(f"/documents/{doc['doc_id']}/figures/9-9.png").status_code == 404


def test_broken_markers_fall_back_to_page_translation(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate(broken=True))
    page = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ru'}).json()['pages'][0]
    assert all(b.get('translated') is None for b in page['blocks'] if b['type'] != 'image')
    assert page['translated'] and '⟦B' not in page['translated']
