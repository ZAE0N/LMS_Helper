"""교안 페이지를 '정리된 교재' 형태로 읽기: 제목 · 글머리표(단계) · 문장 · 코드 · 그림.

슬라이드 PDF는 글자를 하나씩 배치해 띄어쓰기가 빠지는 경우가 많아 글자 간격으로 다시 넣고,
그림·도표는 그 영역을 잘라 그림으로 쓴다 (그림 안의 글자는 번역하지 않음).
"""
import re
import statistics

import pymupdf

SYMBOL = re.compile(r'[-□▪●•‣⁃➢✓✔▶►→]+')
MONO = re.compile(r'cour|mono|consol|d2coding|menlo|fixed', re.I)
MARK = re.compile(r'⟦B(\d+)⟧')
TOP, BOTTOM = 0.15, 0.88   # 머리 장식 / 꼬리말 경계 (페이지 높이 비율)
FOOTER = 0.9


def line_text(line: dict) -> str:
    """글자 사이 간격이 글자 크기의 18%보다 넓으면 띄어쓰기로 봄"""
    out, last = '', None
    for span in line['spans']:
        for ch in span.get('chars', []):
            if last is not None and ch['bbox'][0] - last > span['size'] * 0.18 and not out.endswith(' '):
                out += ' '
            out += ch['c']
            last = ch['bbox'][2]
    return re.sub(r'\s+', ' ', out).strip()


def figure_regions(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """본문에 있는 그림과 도형 묶음(다이어그램). 배경·띠·제목 그림자는 제외"""
    width, height = page.rect.width, page.rect.height
    spans = [pymupdf.Rect(s['bbox']) for b in page.get_text('dict')['blocks'] if b['type'] == 0
             for line in b['lines'] for s in line['spans'] if s['text'].strip()]
    regions = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info['bbox'])
        if r.is_empty or r.width > width * 0.9 or r.height > height * 0.9 or r.get_area() < 2500:
            continue
        if r.y1 < TOP * height or r.y0 > BOTTOM * height:
            continue
        if any(r.contains(t) and r.height < t.height * 3 for t in spans):
            continue  # 글자를 감싸는 작은 그림 = 그림자·광택 효과
        regions.append(r)
    shapes = [pymupdf.Rect(d['rect']) for d in page.get_drawings()
              if d['rect'].width < width * 0.9 and d['rect'].height < height * 0.9
              and TOP * height < d['rect'].y0 < BOTTOM * height
              and min(d['rect'].width, d['rect'].height) > 3]  # 밑줄 같은 가는 선은 제외
    if len(shapes) >= 3:
        union = pymupdf.Rect(shapes[0])
        for r in shapes[1:]:
            union = union | r
        if union.get_area() > 8000:
            regions.append(union + (-4, -4, 4, 4))
    merged: list[pymupdf.Rect] = []
    for r in sorted(regions, key=lambda r: (r.y0, r.x0)):
        hit = next((i for i, m in enumerate(merged) if m.intersects(r)), None)
        if hit is None:
            merged.append(pymupdf.Rect(r))
        else:
            merged[hit] = merged[hit] | r
    return merged


def page_blocks(page: pymupdf.Page) -> list[dict]:
    height = page.rect.height
    figures = figure_regions(page)
    items: list[dict] = [{'type': 'image', 'y': r.y0, 'x': r.x0, 'rect': r} for r in figures]
    for block in page.get_text('rawdict')['blocks']:
        if block['type'] != 0:
            continue
        box = pymupdf.Rect(block['bbox'])
        if box.y0 > FOOTER * height:
            continue
        if any((f & box).get_area() > box.get_area() * 0.5 for f in figures):
            continue  # 도표 안의 글자는 그림으로 보여 줌
        for line in block['lines']:
            raw = line_text(line)
            bullet = bool(SYMBOL.match(raw))
            text = SYMBOL.sub(' ', raw).strip() if bullet else raw
            if not text:
                continue
            items.append({'type': 'text', 'y': line['bbox'][1], 'x': line['bbox'][0], 'text': text, 'bullet': bullet,
                          'size': max(s['size'] for s in line['spans']),
                          'mono': any(MONO.search(s['font']) for s in line['spans'])})
    items.sort(key=lambda it: (round(it['y'] / 4), it['x']))

    merged: list[dict] = []
    for it in items:
        prev = merged[-1] if merged else None
        if prev and it['type'] == prev['type'] == 'text' and it['mono'] and prev['mono'] \
                and 0 < it['y'] - prev['last_y'] < prev['size'] * 2.2:
            prev['text'] += '\n' + it['text']  # 이어지는 코드 줄은 한 덩어리
            prev['last_y'] = it['y']
            continue
        if prev and it['type'] == prev['type'] == 'text' and not it['mono'] and not prev['mono'] \
                and abs(it['size'] - prev['size']) < 1:
            same_line = abs(it['y'] - prev['y']) < 4
            follows = not it['bullet'] and 0 < it['y'] - prev['last_y'] < prev['size'] * 1.6 and it['x'] >= prev['x'] - 2
            if same_line or follows:
                prev['text'] += ' ' + it['text']
                prev['last_y'] = it['y']
                continue
        it['last_y'] = it['y']
        merged.append(it)

    texts = [it for it in merged if it['type'] == 'text']
    top = [it for it in texts if it['y'] < 0.2 * height and not it['bullet'] and not it['mono']]
    title = max(top, key=lambda it: it['size']) if top else None
    rest = [it['size'] for it in texts if it is not title]
    body = statistics.median(rest) if rest else 0
    lefts = sorted({round(it['x']) for it in texts if not it['mono']})
    blocks = []
    for it in merged:
        if it['type'] == 'image':
            blocks.append({'type': 'image', 'rect': [round(v, 1) for v in it['rect']]})
            continue
        level = min(3, sum(1 for x in lefts if x < it['x'] - 8))
        if it['mono']:
            kind = 'code'
        elif it is title and it['size'] >= max(body * 1.15, 18):
            kind = 'heading'
        else:
            kind = 'bullet' if it['bullet'] else 'text'
        blocks.append({'type': kind, 'level': 0 if kind == 'heading' else level, 'text': it['text']})
    return blocks


def analyze_pdf(data: bytes) -> list[dict]:
    """[{page, text, blocks}] — text는 띄어쓰기를 되살린 페이지 전체 글 (챗봇·복습에 사용)"""
    pages = []
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        for i, page in enumerate(doc):
            blocks = page_blocks(page)
            text = '\n'.join(b['text'] for b in blocks if b['type'] != 'image')
            pages.append({'page': i + 1, 'text': text, 'blocks': blocks})
    return pages


URL = re.compile(r'https?://\S+|www\.\S+')


def needs_translation(block: dict) -> bool:
    """제목·글머리표·문장 중 글자가 있는 것만 (주소·숫자만 있는 줄과 코드는 원문 유지)"""
    return block['type'] in ('heading', 'bullet', 'text') and bool(re.search(r'[^\W\d_]{2,}', URL.sub('', block['text'])))


def marked_text(blocks: list[dict]) -> str:
    """번역할 글 덩어리마다 ⟦B번호⟧ 표시를 붙여 한 번에 번역"""
    return '\n\n'.join(f"⟦B{i}⟧ {b['text']}" for i, b in enumerate(blocks) if needs_translation(b))


def split_marked(translated: str, blocks: list[dict]) -> list[str | None] | None:
    """번역 결과를 덩어리별로 나눔. 표시가 빠지거나 섞이면 None (페이지 전체 번역으로 대체).
    번역하지 않은 글 덩어리는 원문, 코드·그림은 None"""
    wanted = [i for i, b in enumerate(blocks) if needs_translation(b)]
    parts = MARK.split(translated)
    found = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if sorted(found) != wanted or any(not found[i] for i in wanted):
        return None
    return [found[i] if i in found else b['text'] if b['type'] in ('heading', 'bullet', 'text') else None
            for i, b in enumerate(blocks)]


def render_figure(data: bytes, page: int, rect: list[float], dpi: int = 130) -> bytes:
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        return doc[page - 1].get_pixmap(clip=pymupdf.Rect(rect), dpi=dpi).tobytes('png')
