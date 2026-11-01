import re
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import figure_ocr, main, service
from app.structure import analyze_pdf, apply_marked, comment_part, marked_text


def slide_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((296, 60), '강의 내용', fontname='korea', fontsize=30)
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


def test_structure_headings_bullets_levels_code_and_figure_labels():
    page = analyze_pdf(slide_pdf())[0]
    blocks = page['blocks']
    assert page['size'] == [720, 540]
    kinds = [(b['type'], b.get('level'), b.get('text')) for b in blocks]
    assert kinds[0] == ('heading', 0, '강의 내용')
    assert blocks[0]['align'] == 'center'
    assert ('bullet', 0, '블록체인 프로그래밍') in kinds          # 빠진 띄어쓰기 복원
    assert ('bullet', 1, '기본 지식') in kinds                    # 들여쓰기 단계
    assert ('code', 1, 'const abi = [1, 2];\nconsole.log(abi);') in kinds
    assert ('label', 0, '이전 블록') in kinds                      # 도표 안 글자
    assert '인하공업전문대학' not in page['text']                   # 꼬리말 제외
    bullet = next(b for b in blocks if b['text'] == '블록체인 프로그래밍')
    x0, y0, x1, y1 = bullet['rect']
    assert x0 >= 69 and bullet['box'][0] == x0 and bullet['box'][2] > x1  # 글머리 기호는 빼고, 오른쪽으로 넓힘
    assert bullet['box'][3] <= next(b for b in blocks if b['text'] == '기본 지식')['rect'][1]
    assert bullet['color'] == '#000000' and bullet['size'] == 20 and page['figures'][0]['raster'] is False


def test_comment_part_finds_korean_comment_body_only():
    def part(line):
        r = comment_part(line)
        return r and line[r[0]:r[1]]
    assert part('x = 1; // 숫자 저장') == '숫자 저장'
    assert part('# 설정 읽기') == '설정 읽기'
    assert part('/* 시작 */') == '시작'
    assert part('<!-- 화면 -->') == '화면'
    assert part('fetch("http://a.com/가") // 요청') == '요청'
    assert part('const url = "http://x"; // no korean') is None
    assert part('#include <stdio.h>') is None


def test_urls_and_numbers_are_not_sent_for_translation():
    blocks = [{'type': 'bullet', 'text': 'https://www.youtube.com/watch?v=XT22h'}, {'type': 'text', 'text': '15%'},
              {'type': 'bullet', 'text': '출석 : 15%'}]
    assert marked_text(blocks) == '⟦B0⟧ 출석 : 15%'
    assert apply_marked('⟦B0⟧ Attendance: 15%', blocks)
    assert [b['translated'] for b in blocks] == [blocks[0]['text'], '15%', 'Attendance: 15%']


def test_marked_round_trip_with_labels_comments_and_mismatch():
    blocks = [{'type': 'heading', 'text': '제목'}, {'type': 'code', 'text': 'x = 1 // 값'}, {'type': 'comment', 'text': '값'},
              {'type': 'label', 'text': '이전 블록'}, {'type': 'bullet', 'text': '항목'}]
    assert marked_text(blocks) == '⟦B0⟧ 제목\n\n⟦B1⟧ 이전 블록\n\n⟦B2⟧ 항목'  # 한 글자 주석은 번역 안 함
    blocks[2]['text'] = '값 저장'
    assert marked_text(blocks) == '⟦B0⟧ 제목\n\n⟦B1⟧ 값 저장\n\n⟦B2⟧ 이전 블록\n\n⟦B3⟧ 항목'
    assert apply_marked('⟦B0⟧ Title\n\n⟦B1⟧ store value\n\n⟦B2⟧ Previous block\n\n⟦B3⟧ Item', blocks)
    assert [b.get('translated') for b in blocks] == ['Title', None, 'store value', 'Previous block', 'Item']
    assert not apply_marked('⟦B0⟧ Title Item', [{'type': 'heading', 'text': '제목'}, {'type': 'bullet', 'text': '항목'}])
    assert not apply_marked('⟦B0⟧ T\n\n⟦B1⟧ ', [{'type': 'heading', 'text': '제목'}, {'type': 'bullet', 'text': '항목'}])


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


def test_documents_have_translated_blocks_and_page_images(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate())
    doc = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ru'}).json()
    page = doc['pages'][0]
    assert page['size'] == [720, 540] and page['blocks'][0]['translated'] == 'RU:강의 내용'
    code = next(b for b in page['blocks'] if b['type'] == 'code')
    assert 'translated' not in code
    assert next(b for b in page['blocks'] if b['type'] == 'label')['translated'] == 'RU:이전 블록'
    assert all('lines' not in b for b in page['blocks'])
    assert '⟦B' not in page['translated'] and 'RU:블록체인 프로그래밍' in page['translated']
    images = []
    for name in (page['image'], page['background']):
        r = client.get(f"/documents/{doc['doc_id']}/figures/{name}")
        assert r.status_code == 200 and r.content.startswith(b'\xff\xd8')
        images.append(r.content)
    assert images[0] != images[1]  # 배경은 글자를 지운 그림
    for bad in ('..%2Fx.jpg', '9.jpg', '1-1.png', '1.png'):
        assert client.get(f"/documents/{doc['doc_id']}/figures/{bad}").status_code == 404


def test_broken_markers_fall_back_to_page_translation(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate(broken=True))
    page = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ru'}).json()['pages'][0]
    assert all('translated' not in b for b in page['blocks'])
    assert page['translated'] and '⟦B' not in page['translated']
    assert page['image'] and 'background' not in page  # 원본 페이지 + 페이지 전체 번역


def photo_pdf() -> bytes:
    """통째로 붙인 그림(파란 바탕) 하나가 있는 슬라이드"""
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((300, 60), '거래 구조', fontname='korea', fontsize=30)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pix.set_rect(pix.irect, (120, 180, 240))
    page.insert_image(pymupdf.Rect(200, 150, 520, 350), pixmap=pix, keep_proportion=False)
    data = doc.tobytes()
    doc.close()
    return data


class FakeOcr:
    """그림 왼쪽 위 (40,40)~(240,100) 픽셀에 '개인키', 영어 글자는 번역 대상 아님"""
    def __call__(self, png):
        assert png.startswith(b'\x89PNG')
        box = [[40, 40], [240, 40], [240, 100], [40, 100]]
        return SimpleNamespace(boxes=[box, box], txts=['개인키', 'Input'], scores=[0.95, 0.99])


def test_raster_figure_text_is_found_by_ocr_and_covered(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate())
    monkeypatch.setenv('FIGURE_OCR', '1')
    monkeypatch.setattr(figure_ocr, '_engine', FakeOcr())
    doc = client.post('/documents', files={'file': ('p.pdf', photo_pdf())}, data={'lang': 'ru'}).json()
    page = doc['pages'][0]
    labels = [b for b in page['blocks'] if b.get('ocr')]
    assert len(labels) == 1 and labels[0]['translated'] == 'RU:개인키'
    x0, y0, x1, y1 = labels[0]['rect']
    assert abs(x0 - (200 + 40 * 72 / 200)) < 1 and abs(y1 - (150 + 100 * 72 / 200)) < 1  # 그림 픽셀 → 페이지 좌표
    assert re.fullmatch(r'#[0-9a-f]{6}', labels[0]['color']) and 'fill' not in labels[0] and 'lines' not in labels[0]
    assert '개인키' not in page['original']  # 챗봇·문제용 글은 PDF 글자만
    background = client.get(f"/documents/{doc['doc_id']}/figures/{page['background']}")
    assert background.status_code == 200


def test_ocr_off_or_failing_keeps_figure_as_is(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate())
    monkeypatch.setenv('FIGURE_OCR', '1')

    def broken(png):
        raise RuntimeError('model download failed')
    monkeypatch.setattr(figure_ocr, '_engine', broken)
    monkeypatch.setattr(figure_ocr, '_failed', False)
    page = client.post('/documents', files={'file': ('p.pdf', photo_pdf())}, data={'lang': 'ru'}).json()['pages'][0]
    assert not any(b.get('ocr') for b in page['blocks']) and page['blocks'][0]['translated'] == 'RU:거래 구조'
    assert figure_ocr._failed


def test_ocr_colors_sample_background_and_text():
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 30), False)
    pix.set_rect(pix.irect, (200, 220, 255))
    pix.set_rect(pymupdf.IRect(20, 10, 40, 20), (10, 10, 10))
    assert figure_ocr._colors(pix, (10, 5, 50, 25)) == ('#c8dcff', '#0a0a0a')
