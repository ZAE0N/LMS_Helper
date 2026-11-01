"""교안 페이지 읽기: 글 덩어리(제목 · 글머리표 · 문장 · 코드 · 그림 속 글자 · 코드 주석)와 그 위치.

- 확장은 글자를 지운 페이지 그림(배경) 위 같은 자리에 번역문을 올려 원본 슬라이드 모양을 유지한다.
- 슬라이드 PDF는 글자를 하나씩 배치해 띄어쓰기가 빠지는 경우가 많아 글자 간격으로 다시 넣는다.
- 모든 페이지에 반복되는 머리·꼬리 장식(로고, 학교명, 과목명 띠)은 그대로 둔다(번역하지 않음).
"""
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from threading import Lock

import pymupdf

SYMBOL_FONT = re.compile(r'wingdings|symbol|webdings|dingbat', re.I)
SYMBOL = re.compile(r'^[-□▪●•‣⁃➢✓✔▶►→\-–·]+')
MONO = re.compile(r'cour|mono|consol|d2coding|menlo|fixed', re.I)
MARK = re.compile(r'⟦B(\d+)⟧')
BOLD = re.compile(r'bold|black|heavy|extrab', re.I)
HANGUL = re.compile(r'[가-힣]')
# 코드 주석 시작: // (주소의 :// 제외), # (#include·#fff 제외), /*, <!--, 여러 줄 주석의 * 줄
COMMENT = re.compile(r'(?<![:\w"\'])//+|(?<![\w&])#+(?![A-Za-z0-9_{])|/\*+|<!--|^\s*\*+(?!/)')
COMMENT_END = re.compile(r'\s*(\*/|-->)\s*$')
URL = re.compile(r'https?://\S+|www\.\S+')
TOP, BOTTOM = 0.15, 0.88  # 머리 장식 / 꼬리 장식 경계 (페이지 높이 비율)
FOOTER = 0.9


@dataclass
class Template:
    """여러 페이지에 반복되는 장식: 그림 xref, 도형 위치, 글 줄(내용+높이)"""
    xrefs: set = field(default_factory=set)
    shapes: set = field(default_factory=set)
    lines: set = field(default_factory=set)


def _shape_key(r: pymupdf.Rect) -> tuple:
    return tuple(round(v / 4) for v in r)


def _line_key(text: str, y: float) -> tuple:
    return re.sub(r'\s+', '', text), round(y / 6)


def find_template(doc: pymupdf.Document) -> Template:
    pages = doc.page_count
    if pages < 4:
        return Template()
    xrefs, shapes, lines = Counter(), Counter(), Counter()
    for page in doc:
        xrefs.update({i['xref'] for i in page.get_image_info(xrefs=True) if i['xref'] > 0})
        shapes.update({_shape_key(pymupdf.Rect(d['rect'])) for d in page.get_drawings()})
        lines.update({_line_key(''.join(s['text'] for s in line['spans']), line['bbox'][1])
                      for b in page.get_text('dict')['blocks'] if b['type'] == 0 for line in b['lines']})
    limit = max(3, pages * 0.5)
    pick = lambda counter: {k for k, n in counter.items() if n >= limit}  # noqa: E731
    return Template(pick(xrefs), pick(shapes), pick(lines))


def line_parts(line: dict) -> tuple[bool, str, list]:
    """(글머리표로 시작하는지, 글, 글자별 상자). 상자[i]는 글[i]의 위치(띄어쓰기는 None).
    글자 사이 간격이 글자 크기의 18%보다 넓으면 띄어쓰기로 봄"""
    chars: list[tuple[str, tuple | None]] = []
    last, bullet, started = None, False, False

    def add(c: str, box: tuple | None) -> None:
        if c.isspace():
            if chars and chars[-1][0] != ' ':
                chars.append((' ', None))
        else:
            chars.append((c, box))

    for span in line['spans']:
        symbol = bool(SYMBOL_FONT.search(span['font']))
        for ch in span.get('chars', []):
            if symbol:  # Wingdings의 q ü Ø 등은 □ ✓ ➢ 기호, 줄 중간의 à 등은 화살표
                if not started:
                    bullet = True
                elif not ch['c'].isspace():
                    add(' ', None), add('→', tuple(ch['bbox'])), add(' ', None)
                last = ch['bbox'][2]
                continue
            if last is not None and ch['bbox'][0] - last > span['size'] * 0.18:
                add(' ', None)
            add(ch['c'], tuple(ch['bbox']))
            started = started or not ch['c'].isspace()
            last = ch['bbox'][2]
    while chars and chars[0][0] == ' ':
        chars.pop(0)
    while chars and chars[-1][0] == ' ':
        chars.pop()
    text = ''.join(c for c, _ in chars)
    if not bullet and (m := SYMBOL.match(text)) and len(text) > len(m.group(0)):
        chars = chars[m.end():]
        while chars and chars[0][0] == ' ':
            chars.pop(0)
        bullet, text = True, ''.join(c for c, _ in chars)
    return bullet, text, [b for _, b in chars]


def _union(boxes: list) -> pymupdf.Rect:
    rect = pymupdf.Rect()
    for b in boxes:
        if b is not None:
            rect |= pymupdf.Rect(b)
    return rect


def _r(rect) -> list[float]:
    return [round(v, 1) for v in rect]


def comment_part(text: str) -> tuple[int, int] | None:
    """코드 줄에서 한글이 있는 주석 내용의 (시작, 끝). 주석 기호(//, # …)는 원문 유지"""
    for m in COMMENT.finditer(text):
        if HANGUL.search(text, m.end()):
            start = len(text) - len(text[m.end():].lstrip())
            end = (e.start() if (e := COMMENT_END.search(text, start)) else len(text))
            return (start, end) if end > start else None
    return None


def figure_regions(page: pymupdf.Page, template: Template) -> list[dict]:
    """본문 그림과 도형 묶음(다이어그램). [{rect, raster}] — raster: 통째로 붙인 그림"""
    width, height = page.rect.width, page.rect.height
    spans = [pymupdf.Rect(s['bbox']) for b in page.get_text('dict')['blocks'] if b['type'] == 0
             for line in b['lines'] for s in line['spans'] if s['text'].strip()]
    found = []
    for info in page.get_image_info(xrefs=True):
        r = pymupdf.Rect(info['bbox'])
        if info['xref'] in template.xrefs or r.is_empty or r.width > width * 0.9 or r.height > height * 0.9:
            continue
        if r.get_area() < 2500 or r.y1 < TOP * height or r.y0 > BOTTOM * height:
            continue
        if any(r.contains(t) and r.height < t.height * 3 for t in spans):
            continue  # 글자를 감싸는 작은 그림 = 그림자·광택 효과
        if r.width > width * 0.8 and r.height < height * 0.25:
            continue  # 페이지 폭의 띠 그림 = 장식
        found.append({'rect': r, 'raster': True})
    drawn = [pymupdf.Rect(d['rect']) + (-0.5, -0.5, 0.5, 0.5) for d in page.get_drawings()  # 선은 폭이 0
             if _shape_key(pymupdf.Rect(d['rect'])) not in template.shapes
             and d['rect'].width < width * 0.9 and d['rect'].height < height * 0.9
             and TOP * height < d['rect'].y0 < BOTTOM * height]
    # 서로 가깝거나 화살표·선으로 이어진 도형끼리 묶음 (다이어그램이 둘이면 따로, 그 사이 글은 본문)
    groups: list[list[pymupdf.Rect]] = []
    for r in drawn:
        near = [g for g in groups if any((x + (-12, -12, 12, 12)).intersects(r) for x in g)]
        for g in near:
            groups.remove(g)
        groups.append(sum(near, []) + [r])
    for g in groups:
        boxes = [r for r in g if min(r.width, r.height) > 4]  # 밑줄·화살표 같은 가는 선은 칸이 아님
        if len(boxes) < 3:
            continue
        union = pymupdf.Rect(boxes[0])
        for r in boxes[1:]:
            union = union | r
        if union.get_area() > 8000:
            found.append({'rect': union + (-4, -4, 4, 4), 'raster': False, 'cells': boxes})
    merged: list[dict] = []
    for f in sorted(found, key=lambda f: (f['rect'].y0, f['rect'].x0)):
        hit = next((m for m in merged if m['rect'].intersects(f['rect'])), None)
        if hit is None:
            merged.append({'rect': pymupdf.Rect(f['rect']), 'raster': f['raster'], 'cells': list(f.get('cells', []))})
        else:
            hit['rect'] = hit['rect'] | f['rect']
            hit['raster'] = hit['raster'] and f['raster']
            hit['cells'] += f.get('cells', [])
    return merged


def _columns(items: list[dict], width: float, height: float) -> list[dict]:
    """왼쪽·오른쪽 두 단으로 나뉜 슬라이드(예: 왼쪽 코드, 오른쪽 설명)는 왼쪽 단을 다 읽고 오른쪽 단을 읽음"""
    head = [it for it in items if it['y'] < 0.18 * height]
    body = [it for it in items if it['y'] >= 0.18 * height]
    left = [it for it in body if it['x'] < width * 0.5]
    right = [it for it in body if it['x'] >= width * 0.5]
    if len(left) < 3 or len(right) < 3:
        return items
    span = lambda group: (min(it['y'] for it in group), max(it['y'] for it in group))  # noqa: E731
    (l0, l1), (r0, r1) = span(left), span(right)
    if min(l1, r1) - max(l0, r0) < 0.5 * min(l1 - l0, r1 - r0):
        return items
    return head + left + right


def _merge_labels(labels: list[dict]) -> list[dict]:
    """그림 속 한 칸에 두 줄로 적힌 글("이전 블록의" + "Hash")은 한 글로 합쳐 번역"""
    out: list[dict] = []
    for label in sorted(labels, key=lambda x: (x['rect'].y0, x['rect'].x0)):
        r = label['rect']
        prev = next((o for o in reversed(out) if abs(o['size'] - label['size']) < 1 and o['text'] != label['text']
                     and -label['size'] * 0.4 <= r.y0 - o['rect'].y1 < label['size'] * 0.6
                     and min(r.x1, o['rect'].x1) - max(r.x0, o['rect'].x0) > 0.5 * min(r.width, o['rect'].width)), None)
        if prev is None:
            out.append(dict(label, lines=list(label['lines'])))
        else:
            prev['text'] += ' ' + label['text']
            prev['rect'] = prev['rect'] | r
            prev['lines'] += label['lines']
    return out


def _style(line: dict) -> dict:
    main = max(line['spans'], key=lambda s: len(s.get('chars', [])))
    return {'color': f"#{main['color']:06x}", 'bold': bool(main['flags'] & 16 or BOLD.search(main['font']))}


def page_blocks(page: pymupdf.Page, template: Template | None = None) -> tuple[list[dict], list[dict]]:
    """(blocks, figures). 블록: {type, level, text, rect, box, size, color, bold, lines}
    - type: heading | bullet | text | code | label(그림 속 글자) | comment(코드 속 한글 주석)
    - box: 번역문이 들어갈 수 있는 자리, lines: 지울 원문 줄 위치"""
    template = template or Template()
    width, height = page.rect.width, page.rect.height
    figures = figure_regions(page, template)
    for f in figures:
        f['labels'] = []
    items: list[dict] = [{'type': 'image', 'y': f['rect'].y0, 'x': f['rect'].x0, 'figure': f} for f in figures]
    lines = [line for block in page.get_text('rawdict')['blocks'] if block['type'] == 0 for line in block['lines']]
    # 글머리 기호만 따로 있는 줄 (기호와 글이 다른 줄로 저장된 PDF). 글 줄보다 뒤에 저장된 경우도 있어 먼저 모음
    marks = []
    for line in lines:
        bullet, text, _ = line_parts(line)
        if bullet and not text:
            marks.append((line['bbox'][1], line['bbox'][2], max(s['size'] for s in line['spans'])))
    for line in lines:
        box = pymupdf.Rect(line['bbox'])
        raw = ''.join(c['c'] for s in line['spans'] for c in s.get('chars', []))
        size = max(s['size'] for s in line['spans'])
        heading = box.y0 < 0.2 * height and size >= 18  # 여러 페이지에 반복되는 단원 제목도 번역
        if box.y0 > FOOTER * height or _line_key(raw, box.y0) in template.lines and not heading:
            continue
        bullet, text, boxes = line_parts(line)
        if not text:
            continue
        rect = _union(boxes)
        base = {'text': text, 'rect': rect, 'size': size, 'lines': [rect], **_style(line)}
        owner = next((f for f in figures if (f['rect'] & box).get_area() > box.get_area() * 0.5), None)
        if owner is not None:  # 그림 안의 글자 → 그림 위 같은 자리에 번역
            owner['labels'].append(base)
            continue
        bullet = bullet or any(abs(y - box.y0) < size * 0.6 and x1 <= box.x0 + 2 and box.x0 - x1 < 40 for y, x1, _ in marks)
        # 코드 = 글머리표 없이 고정폭 글꼴로 시작하는 줄 (주석 속 한글은 다른 글꼴로 대체되므로 첫 글자로 판단)
        first = next((s for s in line['spans'] if any(not c['c'].isspace() for c in s.get('chars', []))
                      and not SYMBOL_FONT.search(s['font'])), None)
        mono = not bullet and first is not None and MONO.search(first['font']) is not None
        comments = []
        if mono and (part := comment_part(text)):
            crect = _union(boxes[part[0]:part[1]])
            comments.append({**base, 'text': text[part[0]:part[1]].strip(), 'rect': crect, 'lines': [crect]})
        items.append({'type': 'text', 'y': box.y0, 'x': box.x0, 'bullet': bullet, 'mono': mono,
                      'comments': comments, **base})
    items.sort(key=lambda it: (round(it['y'] / 4), it['x']))
    items = _columns(items, width, height)

    merged: list[dict] = []
    for it in items:
        prev = merged[-1] if merged else None
        join = None
        if prev and it['type'] == prev['type'] == 'text' and it['mono'] and prev['mono'] \
                and 0 < it['y'] - prev['last_y'] < prev['size'] * 2.2:
            join = '\n'  # 이어지는 코드 줄은 한 덩어리
        elif prev and it['type'] == prev['type'] == 'text' and not it['mono'] and not prev['mono'] \
                and abs(it['size'] - prev['size']) < 1 and not it['bullet']:
            same_line = abs(it['y'] - prev['y']) < 4
            follows = 0 < it['y'] - prev['last_y'] < prev['size'] * 1.6 and it['x'] >= prev['x'] - 2
            join = ' ' if same_line or follows else None
        if join:
            prev['text'] += join + it['text']
            prev['rect'] = prev['rect'] | it['rect']
            prev['lines'] += it['lines']
            prev['comments'] += it['comments']
            prev['last_y'] = it['y']
            continue
        it['last_y'] = it['y']
        merged.append(it)

    texts = [it for it in merged if it['type'] == 'text']
    top = [it for it in texts if it['y'] < 0.2 * height and not it['bullet'] and not it['mono']]
    title = max(top, key=lambda it: it['size']) if top else None
    rest = [it['size'] for it in texts if it is not title]
    body = statistics.median(rest) if rest else 0
    if title is not None and title['size'] < max(body * 1.15, 18):
        title = None
    if title is None and texts and not texts[0]['mono'] and texts[0]['size'] >= max(it['size'] for it in texts) \
            and texts[0]['size'] > body:
        title = texts[0]  # 머리 장식을 뺀 뒤 맨 위의 가장 큰 항목 (예: "□ Transaction")
    lefts = sorted({round(it['x']) for it in texts if not it['mono'] and it is not title})

    def block(kind: str, level: int, it: dict) -> dict:
        return {'type': kind, 'level': level, 'text': it['text'], 'rect': it['rect'], 'size': round(it['size'], 1),
                'color': it['color'], 'bold': it['bold'], 'lines': it['lines']}

    panels = [pymupdf.Rect(d['rect']) for d in page.get_drawings()] if any(it.get('comments') for it in merged) else []
    blocks = []
    for it in merged:
        if it['type'] == 'image':
            for label in _merge_labels(it['figure']['labels']):
                b = block('label', 0, label)
                b['cell'] = _cell(label['rect'], it['figure'].get('cells', []))
                b['wrap'] = len(label['lines']) > 1 or b['cell'] is not None
                blocks.append(b)
            continue
        level = 0 if it is title else min(3, sum(1 for x in lefts if x < it['x'] - 8))
        kind = 'heading' if it is title else 'code' if it['mono'] else 'bullet' if it['bullet'] else 'text'
        blocks.append(block(kind, level, it))
        if it['comments']:  # 주석 번역은 코드 영역(배경 상자가 있으면 그 상자) 안에서만
            panel = min((d for d in panels if d.contains(it['rect'])), key=lambda d: d.get_area(), default=None)
            limit = panel.x1 - 6 if panel is not None else it['rect'].x1 + 4
            blocks += [dict(block('comment', level, c), limit=limit) for c in it['comments']]
    _boxes(blocks, figures, width, height)
    for b in blocks:
        b['rect'] = _r(b['rect'])
        b['lines'] = [_r(r) for r in b['lines']]
    return blocks, [{'rect': _r(f['rect']), 'raster': f['raster']} for f in figures]


def _cell(r: pymupdf.Rect, cells: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    """그림 속 글자를 감싸는 가장 작은 칸(도형). 번역문은 이 칸 안에 넣음"""
    c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    inside = [x for x in cells if x.contains(c) and x.width >= r.width * 0.8 and x.height >= r.height * 0.8
              and x.get_area() < r.get_area() * 40]
    return min(inside, key=lambda x: x.get_area()) if inside else None


def label_box(r: pymupdf.Rect, size: float) -> pymupdf.Rect:
    return pymupdf.Rect(r.x0 - 2, r.y0 - size * 0.3, r.x1 + 2, r.y1 + size * 0.3)


def _boxes(blocks: list[dict], figures: list[dict], width: float, height: float) -> None:
    """번역문이 들어갈 자리(box). 글 덩어리는 오른쪽·아래로 다른 글이나 그림을 만날 때까지 넓힘
    (번역문은 보통 한국어보다 길어서). 그림 속 글자는 원래 칸, 코드 주석은 한 줄"""
    others = [(b, b['rect']) for b in blocks if b['type'] != 'comment'] + [(None, f['rect']) for f in figures]
    for b in blocks:
        r, s = b['rect'], b['size']
        if b['type'] == 'label':
            b['align'] = 'center'
            cell = b.pop('cell', None)
            box = pymupdf.Rect(cell.x0 + 3, max(cell.y0 + 1, r.y0 - s * 0.6), cell.x1 - 3, min(cell.y1 - 1, r.y1 + s * 0.6)) \
                if cell is not None else label_box(r, s)
            if box.width < r.width or box.height < r.height:
                box = label_box(r, s)
            elif cell is not None and r.x0 - cell.x0 < (cell.x1 - r.x1) * 0.5 - 2:
                b['align'] = 'left'  # 표의 왼쪽 정렬 칸
        elif b['type'] == 'comment':
            box = pymupdf.Rect(r.x0, r.y0 - 1, max(b.pop('limit', width - 6), r.x1), r.y1 + 1)
        elif b['type'] in ('heading', 'text') and abs((r.x0 + r.x1) / 2 - width / 2) < width * 0.04 and r.x0 > width * 0.12:
            # 가운데 정렬된 제목·부제목: 가운데를 기준으로 양쪽으로 넓힘
            cx = (r.x0 + r.x1) / 2
            left = max([o.x1 + 6 for x, o in others if x is not b and o.x1 <= r.x0 + 2
                        and o.y0 < r.y1 and o.y1 > r.y0] + [width * 0.06])
            right = min([o.x0 - 6 for x, o in others if x is not b and o.x0 >= r.x1 - 2
                         and o.y0 < r.y1 and o.y1 > r.y0] + [width * 0.94])
            half = max(min(cx - left, right - cx), r.width / 2)
            bottom = min([o.y0 - 2 for x, o in others if x is not b and o.y0 >= r.y1 - 2
                          and o.x0 < cx + half and o.x1 > cx - half] + [FOOTER * height])
            box = pymupdf.Rect(cx - half, r.y0, cx + half, max(bottom, r.y1))
            b['align'] = 'center'
        else:
            margin = max(min(r.x0, width * 0.06), 10)
            right = min([o.x0 - 6 for x, o in others if x is not b and o.x0 >= r.x1 - 2
                         and o.y0 < r.y1 and o.y1 > r.y0] + [width - margin])
            right = max(right, r.x1)
            bottom = min([o.y0 - 2 for x, o in others if x is not b and o.y0 >= r.y1 - 2
                          and o.x0 < right and o.x1 > r.x0] + [FOOTER * height])
            box = pymupdf.Rect(r.x0, r.y0, right, max(bottom, r.y1))
        b['box'] = _r(box)


def analyze_pdf(data: bytes) -> list[dict]:
    """[{page, text, size, blocks, figures}] — text는 띄어쓰기를 되살린 페이지 전체 글 (챗봇·복습에 사용)"""
    pages = []
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        template = find_template(doc)
        for i, page in enumerate(doc):
            blocks, figures = page_blocks(page, template)
            text = '\n'.join(b['text'] for b in blocks if b['type'] != 'comment')
            pages.append({'page': i + 1, 'text': text, 'size': [round(page.rect.width, 1), round(page.rect.height, 1)],
                          'blocks': blocks, 'figures': figures})
    return pages


def needs_translation(text: str) -> bool:
    """글자가 있는 것만 (주소·숫자만 있는 줄은 원문 유지)"""
    return bool(re.search(r'[^\W\d_]{2,}', URL.sub('', text)))


TRANSLATABLE = ('heading', 'bullet', 'text', 'label', 'comment')


def units(blocks: list[dict]) -> list[tuple[int, str]]:
    """번역 단위 (블록 번호, 글). 코드는 원문 유지 (한글 주석만 따로 번역)"""
    return [(i, b['text']) for i, b in enumerate(blocks) if b['type'] in TRANSLATABLE and needs_translation(b['text'])]


def marked_text(blocks: list[dict]) -> str:
    """번역 단위마다 ⟦B번호⟧ 표시를 붙여 한 번에 번역"""
    return '\n\n'.join(f'⟦B{n}⟧ {text}' for n, (_, text) in enumerate(units(blocks)))


def apply_marked(translated: str, blocks: list[dict]) -> bool:
    """번역 결과를 블록의 translated에 넣음. 표시가 어긋나면 False"""
    wanted = units(blocks)
    parts = MARK.split(translated)
    found = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if sorted(found) != list(range(len(wanted))) or not all(found.values()):
        return False
    for b in blocks:
        if b['type'] in TRANSLATABLE:
            b['translated'] = b['text']  # 번역 대상이 아니면 원문 그대로
    for n, (i, _) in enumerate(wanted):
        blocks[i]['translated'] = found[n]
    return True


_RENDER_LOCK = Lock()  # PyMuPDF는 여러 스레드에서 동시에 쓰면 안전하지 않음


def render_page(data: bytes, page: int, erase: list[list[list[float]]], fills: list[tuple[list[float], str]] = (),
                zoom: float = 2.0) -> tuple[bytes, bytes | None]:
    """(원본 페이지 JPEG, 번역할 글자를 지운 배경 JPEG 또는 None).
    erase: 블록마다 지울 줄 위치. 글자를 감싸는 그림자·광택 그림도 지움. 그림·도형·글머리 기호·장식은 남김.
    fills: 그림 속 글자(OCR) 자리를 둘레 색으로 덮음"""
    with _RENDER_LOCK, pymupdf.open(stream=data, filetype='pdf') as doc:
        p = doc[page - 1]
        matrix = pymupdf.Matrix(zoom, zoom)
        original = p.get_pixmap(matrix=matrix).tobytes('jpeg', jpg_quality=85)
        if not erase and not fills:
            return original, None
        rects = [pymupdf.Rect(r) for lines in erase for r in lines]
        groups = [_union(lines) for lines in erase if lines]

        def shadow_xrefs() -> list[int]:
            verdict: dict[int, bool] = {}
            for info in p.get_image_info(xrefs=True):
                image = pymupdf.Rect(info['bbox'])
                if info['xref'] <= 0 or image.is_empty:
                    continue
                # 제목 그림자는 글자(또는 낱말)마다 따로 붙은 그림인 경우가 많음 → 블록 둘레 안에 들어가는 그림
                shadow = image.width < p.rect.width * 0.8 and any(
                    (image & g).get_area() > 0.3 * image.get_area() and image.height < min(g.height, 60) * 3
                    and (g + (-g.height, -g.height, g.height, g.height)).contains(image) for g in groups)
                verdict[info['xref']] = verdict.get(info['xref'], True) and shadow
            return [x for x, shadow in verdict.items() if shadow]

        for r in rects:  # 위아래 줄·글머리 기호를 건드리지 않게 줄 안쪽만
            p.add_redact_annot(pymupdf.Rect(r.x0 + 1, r.y0 + r.height * 0.25, r.x1 - 0.5, r.y1 - r.height * 0.25), fill=False)
        if rects:
            p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        done: set[int] = set()
        for _ in range(4):  # 그림자가 여러 겹(그림 + 마스크)이면 지운 뒤 다시 확인
            todo = [x for x in shadow_xrefs() if x not in done]
            if not todo:
                break
            for xref in todo:
                p.delete_image(xref)
                done.add(xref)
        for rect, color in fills:
            p.draw_rect(pymupdf.Rect(rect), color=None, fill=tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)))
        return original, p.get_pixmap(matrix=matrix).tobytes('jpeg', jpg_quality=85)
