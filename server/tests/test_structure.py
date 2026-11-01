import re
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import figure_ocr, main, service
from app.structure import analyze_pdf, apply_marked, code_parts, comment_part, marked_text, render_page


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
    for x in (100, 300, 500):  # 화살표로 이어진 도형 묶음 = 다이어그램
        page.draw_rect(pymupdf.Rect(x, 280, x + 120, 380), color=(0, 0, 1), width=2)
        if x < 500:
            page.draw_line((x + 120, 330), (x + 200, 330), color=(0, 0, 0), width=1)
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


def test_broken_markers_are_retried_unit_by_unit(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate(broken=True))
    asked = []
    monkeypatch.setattr(service, 'translate_text', lambda text, *_: asked.append(text) or {'translated': f'RU:{text}', 'terms': [], 'issues': []})
    page = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ru'}).json()['pages'][0]
    assert '강의 내용' in asked and all('⟦B' not in t for t in asked)
    assert page['blocks'][0]['translated'] == 'RU:강의 내용' and page['background']


def test_broken_markers_fall_back_to_page_translation(setup, monkeypatch):
    client = setup
    monkeypatch.setattr(service, 'translate_page', fake_translate(broken=True))
    monkeypatch.setattr(service, 'retry_marked', lambda *_: None)
    page = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ru'}).json()['pages'][0]
    assert all('translated' not in b for b in page['blocks'])
    assert page['translated'] and '⟦B' not in page['translated']
    assert page['image'] and 'background' not in page  # 원본 페이지 + 페이지 전체 번역


def photo_pdf(color: tuple[int, int, int] = (120, 180, 240)) -> bytes:
    """통째로 붙인 그림(파란 바탕) 하나가 있는 슬라이드"""
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((300, 60), '거래 구조', fontname='korea', fontsize=30)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pix.set_rect(pix.irect, color)
    page.insert_image(pymupdf.Rect(200, 150, 520, 350), pixmap=pix, keep_proportion=False)
    data = doc.tobytes()
    doc.close()
    return data


def test_same_text_with_different_images_uses_distinct_documents_but_reuses_translation(setup, monkeypatch):
    calls = []
    translate = fake_translate()

    def record(*args):
        calls.append(args[0])
        return translate(*args)

    monkeypatch.setattr(service, 'translate_page', record)
    first = setup.post('/documents', files={'file': ('a.pdf', photo_pdf())}, data={'lang': 'ru'}).json()
    second = setup.post('/documents', files={'file': ('b.pdf', photo_pdf((240, 100, 100)))}, data={'lang': 'ru'}).json()
    assert first['pages'][0]['original'] == second['pages'][0]['original']
    assert first['doc_id'] != second['doc_id'] and len(calls) == 1
    images = [(service.figures_dir(d['doc_id']) / d['pages'][0]['image']).read_bytes() for d in (first, second)]
    assert images[0] != images[1]


def test_document_cache_is_invalidated_when_translation_prompt_changes(setup, monkeypatch):
    monkeypatch.setattr(service, 'translate_page', fake_translate())
    data = slide_pdf()
    first = service.translate_pdf(data, 'ru', None, object())
    monkeypatch.setattr(service, 'PROMPT_VERSION', service.PROMPT_VERSION + '-new')
    second = service.translate_pdf(data, 'ru', None, object())
    assert first['doc_id'] != second['doc_id']


class FakeOcr:
    """그림 왼쪽 위 (40,40)~(240,100) 픽셀에 '개인키', 영어 글자는 번역 대상 아님"""
    def __call__(self, png, **_):
        assert png.startswith(b'\x89PNG')
        off = round((pymupdf.Pixmap(png).width - 320 * figure_ocr.DPI / 72) / 2)  # 둘레를 넓혀 찍은 그림이면 그만큼 밀림
        box = [[40 + off, 40 + off], [240 + off, 40 + off], [240 + off, 100 + off], [40 + off, 100 + off]]
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
    scale = 72 / figure_ocr.DPI
    assert abs(x0 - (200 + 40 * scale)) < 1 and abs(y1 - (150 + 100 * scale)) < 1  # 그림 픽셀 → 페이지 좌표
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


def test_ocr_engine_uses_supported_korean_model(monkeypatch):
    import rapidocr
    seen = {}
    monkeypatch.setattr(rapidocr, 'RapidOCR', lambda params: seen.update(params) or object())
    monkeypatch.setattr(figure_ocr, '_engine', None)
    monkeypatch.setattr(figure_ocr, '_failed', False)
    assert figure_ocr._get_engine() is not None
    assert str(seen['Rec.lang_type'].value) == 'korean' and str(seen['Rec.model_type'].value) == 'mobile'


def test_code_korean_runs_are_translated_and_line_is_rewritten():
    text = 'console.log("서명 계정:", a); // 주소 출력'
    boxes = [(i * 6.0, 0, i * 6.0 + 6, 10) for i in range(len(text))]
    parts = code_parts(text, boxes, {'size': 10, 'color': '#000000', 'bold': False})
    kinds = [(p['kind'], p['text']) for p in parts]
    assert kinds == [('comment', '주소 출력'), ('codetext', '서명 계정:'), ('codeline', 'console.log("서명 계정:", a);')]
    line = parts[2]
    blocks = [{'type': 'comment', 'text': '주소 출력'}, {'type': 'codetext', 'text': '서명 계정:'},
              {'type': 'codeline', 'text': line['text'], 'runs': line['runs'], 'parts': [1]}]
    assert marked_text(blocks) == '⟦B0⟧ 주소 출력\n\n⟦B1⟧ 서명 계정:'
    assert apply_marked('⟦B0⟧ print address\n\n⟦B1⟧ Signer account:', blocks)
    assert blocks[2]['translated'] == 'console.log("Signer account:", a);'
    assert line['stop'] == text.index('//') * 6.0  # 다시 쓴 줄은 주석 앞에서 멈춤


def test_footer_is_translated_but_not_in_page_text_and_columns_do_not_overlap():
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    for y, (left, right) in enumerate([('왼쪽 첫째 항목', '오른쪽 첫째 항목'), ('왼쪽 둘째 항목', '오른쪽 둘째 항목'),
                                       ('왼쪽 셋째 항목', '오른쪽 셋째 항목')]):
        page.insert_text((60, 140 + y * 40), left, fontname='korea', fontsize=18)
        page.insert_text((380, 140 + y * 40), '□', fontname='korea', fontsize=18)
        page.insert_text((405, 140 + y * 40), right, fontname='korea', fontsize=18)
    page.insert_text((60, 520), '인하공업전문대학', fontname='korea', fontsize=10)
    page.insert_text((660, 520), '3', fontname='korea', fontsize=10)
    p = analyze_pdf(doc.tobytes())[0]
    footer = next(b for b in p['blocks'] if b['text'] == '인하공업전문대학')
    assert footer.get('deco') and '인하공업전문대학' not in p['text']
    assert footer['box'][2] < 660  # 쪽 번호를 덮지 않음
    left = next(b for b in p['blocks'] if b['text'] == '왼쪽 첫째 항목')
    assert left['box'][2] < 380  # 오른쪽 단의 글머리 기호 앞에서 멈춤


def test_korean_in_code_keeps_context_of_strings_and_html():
    from app.structure import korean_spans
    def spans(t):
        return [t[a:b] for a, b in korean_spans(t, len(t))]
    assert spans('el.textContent = `chain ${id} 에 기록이 없음 → MetaMask에서 전환`;') == \
        ['chain ${id} 에 기록이 없음 → MetaMask에서 전환']  # 문자열 전체 (영어 단어 포함)
    assert spans('<button id="connectBtn">MetaMask 연결</button>') == ['MetaMask 연결']
    assert spans('w(\'<p style="color:red">안내 문구</p>\')') == ['안내 문구']  # 문자열 속 HTML은 태그 사이만
    assert spans('m.call(컨트랙트, "함수명", [인자])') == ['컨트랙트', '함수명', '인자']


def test_single_korean_letter_in_figure_is_translated():
    blocks = [{'type': 'label', 'text': '홀'}, {'type': 'bullet', 'text': '가'}, {'type': 'label', 'text': '7'}]
    assert marked_text(blocks) == '⟦B0⟧ 홀'


def test_korean_keeps_original_without_calling_llm(setup, monkeypatch):
    client = setup

    def no_llm(*args):
        raise AssertionError('ko must not translate')
    monkeypatch.setattr(service, 'translate_page', no_llm)
    doc = client.post('/documents', files={'file': ('a.pdf', slide_pdf())}, data={'lang': 'ko'}).json()
    page = doc['pages'][0]
    assert doc['lang'] == 'ko' and page['translated'] == page['original'] and 'background' not in page
    assert all('translated' not in b for b in page['blocks'])
    assert client.get(f"/documents/{doc['doc_id']}/figures/{page['image']}").status_code == 200


def test_table_drawn_with_lines_keeps_each_cell_separate_and_inside():
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=540)
    xs, ys = [60, 230, 560, 900], [110, 150, 210, 270]
    shape = page.new_shape()  # 표 선 전체를 한 경로로 그림 (PPT 표에서 흔함)
    for x in xs:
        shape.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        shape.draw_line((xs[0], y), (xs[-1], y))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    rows = [['방법', '설명', '주의할 점'], ['결측치 제거', '빈 행을 삭제한다', '데이터가 줄어듦'], ['평균 대체', '평균으로 채운다', '분산이 작아짐']]
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            page.insert_text((xs[j] + 8, ys[i] + 26), text, fontname='korea', fontsize=13)
    blocks = analyze_pdf(doc.tobytes())[0]['blocks']
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            b = next(b for b in blocks if b['text'] == text)  # 같은 줄의 칸끼리 합치지 않음
            assert xs[j] <= b['box'][0] and b['box'][2] <= xs[j + 1]
            assert b['box'][3] <= ys[i + 1]


def test_card_text_stays_in_its_card_and_line_behind_cards_is_ignored():
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=540)
    page.draw_line((40, 300), (920, 300), color=(0.6, 0.6, 0.6), width=2)  # 카드 뒤를 지나는 선
    for k, (head, body) in enumerate([('1단계 수집', '자료를 모은다'), ('2단계 정제', '결측치를 처리한다'), ('3단계 분석', '패턴을 찾는다')]):
        x0 = 70 + k * 290
        page.draw_rect(pymupdf.Rect(x0, 130, x0 + 250, 420), color=(0.2, 0.3, 0.7), fill=(1, 1, 1))
        page.draw_rect(pymupdf.Rect(x0, 130, x0 + 250, 180), color=None, fill=(0.2, 0.3, 0.7))
        page.insert_text((x0 + 16, 163), head, fontname='korea', fontsize=18, color=(1, 1, 1))
        page.insert_text((x0 + 16, 220), body, fontname='korea', fontsize=14)
    blocks = analyze_pdf(doc.tobytes())[0]['blocks']
    for k, (head, body) in enumerate([('1단계 수집', '자료를 모은다'), ('2단계 정제', '결측치를 처리한다'), ('3단계 분석', '패턴을 찾는다')]):
        x0 = 70 + k * 290
        h = next(b for b in blocks if b['text'] == head)  # 흰 글자는 색 띠 안에만 (밖으로 나가면 안 보임)
        assert x0 <= h['box'][0] and h['box'][2] <= x0 + 250 and h['box'][3] <= 180
        b = next(b for b in blocks if b['text'] == body)
        assert b['box'][2] <= x0 + 250 and b['box'][3] > 300  # 카드 뒤의 선은 벽이 아님


def test_bullet_list_in_box_is_split_per_item_left_aligned_and_highlights_are_erased():
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=540)
    page.draw_rect(pymupdf.Rect(100, 100, 860, 460), color=(0.4, 0.7, 0.9), dashes='[4] 0')  # 점선 상자
    page.draw_rect(pymupdf.Rect(140, 140, 820, 420), color=None, fill=(0.95, 0.95, 0.95))
    page.draw_rect(pymupdf.Rect(176, 158, 330, 182), color=None, fill=(0.76, 0.91, 0.98))  # 형광 표시
    items = ['스위치 부근에 물질을 놓지 않는다', '분전반 안에 물건을 두지 않는다', '전원을 차단하고 연락한다']
    for k, text in enumerate(items):
        page.insert_text((160, 176 + k * 30), '- ' + text, fontname='korea', fontsize=16)
    p = analyze_pdf(doc.tobytes())[0]
    blocks = [b for b in p['blocks'] if b['text'] in items]
    assert len(blocks) == 3 and all(b['type'] == 'bullet' and b.get('align') != 'center' for b in blocks)
    assert blocks[0]['hl'] and not blocks[1]['hl']
    _, plain = render_page(doc.tobytes(), 1, [blocks[1]['lines']])
    _, cleaned = render_page(doc.tobytes(), 1, [blocks[0]['lines']], covers=blocks[0]['hl'])
    def blue(jpeg: bytes) -> int:
        pix = pymupdf.Pixmap(jpeg)
        return sum(1 for x in range(360, 640, 4) for y in range(320, 360, 4) if pix.pixel(x, y)[2] > 240 and pix.pixel(x, y)[0] < 210)
    assert blue(plain) > 50 and blue(cleaned) == 0


def test_loose_line_spacing_and_centered_titles_join_into_one_unit():
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=540)
    page.insert_text((100, 300), '- 연구실 안전유지 관리 및', fontname='korea', fontsize=18)
    page.insert_text((100, 333), '사고예방을 위한 안전환경', fontname='korea', fontsize=18)  # 줄 간격 1.8배
    page.insert_text((400, 150), '연구실안전관리담당자', fontname='korea', fontsize=26)
    page.insert_text((430, 183), '실습실 책임자', fontname='korea', fontsize=26)  # 가운데 정렬된 둘째 줄
    texts = [b['text'] for b in analyze_pdf(doc.tobytes())[0]['blocks']]
    assert '연구실 안전유지 관리 및 사고예방을 위한 안전환경' in texts
    assert '연구실안전관리담당자 실습실 책임자' in texts


def test_ocr_two_line_titles_are_joined_even_from_different_images():
    def label(text, rect):
        return {'type': 'label', 'text': text, 'rect': rect, 'size': 27.0, 'lines': [rect], 'box': rect, 'ocr': True, 'fill': '#ffffff'}
    out = figure_ocr._stack([label('데이터 분석과', [600, 462, 790, 500]), label('접근법', [679, 510, 772, 547]),
                             label('데이터', [1188, 461, 1278, 500])])
    assert sorted(x['text'] for x in out) == ['데이터', '데이터 분석과 접근법']
    joined = next(x for x in out if x['text'] == '데이터 분석과 접근법')
    assert joined['rect'] == [600, 462, 790, 547] and len(joined['lines']) == 2 and joined['wrap']


def test_ocr_label_room_grows_to_the_table_cell_in_the_picture():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=300)
    page.draw_rect(pymupdf.Rect(100, 100, 300, 160), color=(0, 0, 0), width=1.5)  # 그림 속 표의 한 칸
    page.draw_rect(pymupdf.Rect(300, 100, 500, 160), color=(0, 0, 0), width=1.5)
    pix = page.get_pixmap(dpi=figure_ocr.SCAN_DPI)
    box = figure_ocr._room(pix, pymupdf.Rect(180, 122, 220, 138), pymupdf.Rect(90, 90, 510, 170), '#ffffff', [])
    assert 100 <= box.x0 < 106 and 294 < box.x1 <= 300 and 100 <= box.y0 < 106 and 154 < box.y1 <= 160
    near = figure_ocr._room(pix, pymupdf.Rect(180, 122, 220, 138), pymupdf.Rect(90, 90, 510, 170), '#ffffff',
                            [pymupdf.Rect(250, 122, 280, 138)])
    assert near.x1 < 250  # 옆 글자 앞에서 멈춤


def test_list_items_form_one_group_with_marks_but_cards_stay_apart():
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=540)
    for k, text in enumerate(['첫째 항목입니다', '둘째 항목입니다', '셋째 항목입니다']):
        page.insert_text((80, 200 + k * 30), '- ' + text, fontname='korea', fontsize=16, color=(0.8, 0, 0))
    for x0 in (500, 740):  # 나란한 카드 두 장: 서로 다른 묶음
        page.draw_rect(pymupdf.Rect(x0, 160, x0 + 200, 400), color=(0, 0, 0.6))
        for k in range(2):
            page.insert_text((x0 + 16, 200 + k * 30), f'- 카드 {x0} 항목 {k}', fontname='korea', fontsize=16)
    blocks = analyze_pdf(doc.tobytes())[0]['blocks']
    items = [b for b in blocks if b['text'].endswith('항목입니다')]
    assert len({b['group'] for b in items}) == 1 and all(b['mark']['char'] == '-' and b['mark']['color'] == '#cc0000' for b in items)
    gx0, gy0, gx1, gy1 = items[0]['gbox']
    assert gx0 <= items[0]['mark']['rect'][0] and gy0 <= items[0]['rect'][1] and gy1 >= items[-1]['rect'][3]
    assert all(items[0]['mark']['rect'] in b['lines'] or b['mark']['rect'] in b['lines'] for b in items)  # 기호도 배경에서 지움
    cards = {b['group'] for b in blocks if b['text'].startswith('카드')}
    assert len(cards) == 2


def test_small_word_pictures_side_by_side_are_read_as_one_line():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 40), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    for x0 in (100, 170, 240):  # "강의" "계획" "안내" 처럼 낱말마다 따로 붙인 그림 (조금씩 겹침)
        page.insert_image(pymupdf.Rect(x0, 200, x0 + 80, 238), pixmap=pix)
    page.insert_image(pymupdf.Rect(100, 300, 180, 338), pixmap=pix)  # 다른 줄
    lines = figure_ocr._strips(page, [pymupdf.Rect(50, 150, 500, 360)])
    assert sorted((round(r.x0 / 10), round(r.x1 / 10), round(r.y0 / 10)) for r in lines) == [(10, 18, 30), (10, 32, 20)]
