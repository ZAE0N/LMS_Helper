"""교안 페이지를 '정리된 교재' 형태로 읽기: 제목 · 글머리표(단계) · 문장 · 코드 · 그림.

- 슬라이드 PDF는 글자를 하나씩 배치해 띄어쓰기가 빠지는 경우가 많아 글자 간격으로 다시 넣는다.
- 모든 페이지에 반복되는 머리·꼬리 장식(로고, 학교명, 과목명 띠)은 뺀다.
- 그림·도표는 영역을 잘라 그림으로 쓴다. 도형으로 그린 그림 안의 글자(labels)는 번역해서 그 자리에 다시 넣고,
  통째로 붙인 그림(사진 등)은 별도로(vision) 처리한다.
"""
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

SYMBOL_FONT = re.compile(r'wingdings|symbol|webdings|dingbat', re.I)
SYMBOL = re.compile(r'^[-□▪●•‣⁃➢✓✔▶►→\-–·]+')
MONO = re.compile(r'cour|mono|consol|d2coding|menlo|fixed', re.I)
MARK = re.compile(r'⟦B(\d+)⟧')
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


def line_parts(line: dict) -> tuple[bool, str]:
    """(글머리표로 시작하는지, 글). 글자 사이 간격이 글자 크기의 18%보다 넓으면 띄어쓰기로 봄"""
    out, last, bullet, started = '', None, False, False
    for span in line['spans']:
        symbol = bool(SYMBOL_FONT.search(span['font']))
        for ch in span.get('chars', []):
            if symbol:  # Wingdings의 q ü Ø 등은 □ ✓ ➢ 기호, 줄 중간의 à 등은 화살표
                if not started:
                    bullet = True
                elif not ch['c'].isspace():
                    out += ' → '
                last = ch['bbox'][2]
                continue
            if last is not None and ch['bbox'][0] - last > span['size'] * 0.18 and not out.endswith(' '):
                out += ' '
            out += ch['c']
            started = started or not ch['c'].isspace()
            last = ch['bbox'][2]
    text = re.sub(r'\s+', ' ', out).strip()
    if not bullet and (m := SYMBOL.match(text)) and len(text) > len(m.group(0)):
        bullet, text = True, text[m.end():].strip()
    return bullet, text


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
    shapes = [pymupdf.Rect(d['rect']) for d in page.get_drawings()
              if _shape_key(pymupdf.Rect(d['rect'])) not in template.shapes
              and d['rect'].width < width * 0.9 and d['rect'].height < height * 0.9
              and TOP * height < d['rect'].y0 < BOTTOM * height
              and min(d['rect'].width, d['rect'].height) > 3]  # 밑줄 같은 가는 선은 제외
    if len(shapes) >= 3:
        union = pymupdf.Rect(shapes[0])
        for r in shapes[1:]:
            union = union | r
        if union.get_area() > 8000:
            found.append({'rect': union + (-4, -4, 4, 4), 'raster': False})
    merged: list[dict] = []
    for f in sorted(found, key=lambda f: (f['rect'].y0, f['rect'].x0)):
        hit = next((m for m in merged if m['rect'].intersects(f['rect'])), None)
        if hit is None:
            merged.append({'rect': pymupdf.Rect(f['rect']), 'raster': f['raster']})
        else:
            hit['rect'] = hit['rect'] | f['rect']
            hit['raster'] = hit['raster'] and f['raster']
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
    for label in sorted(labels, key=lambda x: (x['rect'][1], x['rect'][0])):
        r = pymupdf.Rect(label['rect'])
        prev = next((o for o in reversed(out) if abs(o['size'] - label['size']) < 1 and o['text'] != label['text']
                     and 0 <= r.y0 - o['rect'][3] < label['size'] * 0.6
                     and min(r.x1, o['rect'][2]) - max(r.x0, o['rect'][0]) > 0.5 * min(r.width, o['rect'][2] - o['rect'][0])), None)
        if prev is None:
            out.append(dict(label))
        else:
            prev['text'] += ' ' + label['text']
            prev['rect'] = [round(v, 1) for v in pymupdf.Rect(prev['rect']) | r]
    return out


def page_blocks(page: pymupdf.Page, template: Template | None = None) -> list[dict]:
    template = template or Template()
    width, height = page.rect.width, page.rect.height
    figures = figure_regions(page, template)
    for f in figures:
        f['labels'] = []
    items: list[dict] = [{'type': 'image', 'y': f['rect'].y0, 'x': f['rect'].x0, 'figure': f} for f in figures]
    marks = []  # 글머리 기호만 따로 있는 줄 (기호와 글이 다른 줄로 저장된 PDF)
    for block in page.get_text('rawdict')['blocks']:
        if block['type'] != 0:
            continue
        for line in block['lines']:
            box = pymupdf.Rect(line['bbox'])
            raw = ''.join(c['c'] for s in line['spans'] for c in s.get('chars', []))
            if box.y0 > FOOTER * height or _line_key(raw, box.y0) in template.lines:
                continue
            bullet, text = line_parts(line)
            size = max(s['size'] for s in line['spans'])
            if not text:
                if bullet:
                    marks.append((box.y0, box.x1, size))
                continue
            owner = next((f for f in figures if (f['rect'] & box).get_area() > box.get_area() * 0.5), None)
            if owner is not None:  # 그림 안의 글자 → 그림 위에 번역해서 다시 그림
                main = max(line['spans'], key=lambda s: len(s.get('chars', [])))
                owner['labels'].append({'text': text, 'rect': [round(v, 1) for v in box], 'size': round(size, 1),
                                        'color': main['color']})
                continue
            bullet = bullet or any(abs(y - box.y0) < size * 0.6 and x1 <= box.x0 + 2 and box.x0 - x1 < 40 for y, x1, _ in marks)
            # 코드 = 글머리표 없이 고정폭 글꼴로 시작하는 줄 (주석 속 한글은 다른 글꼴로 대체되므로 첫 글자로 판단)
            first = next((s for s in line['spans'] if any(not c['c'].isspace() for c in s.get('chars', []))
                          and not SYMBOL_FONT.search(s['font'])), None)
            mono = not bullet and first is not None and MONO.search(first['font']) is not None
            items.append({'type': 'text', 'y': box.y0, 'x': box.x0, 'text': text, 'bullet': bullet, 'size': size,
                          'mono': mono})
    items.sort(key=lambda it: (round(it['y'] / 4), it['x']))
    items = _columns(items, width, height)

    merged: list[dict] = []
    for it in items:
        prev = merged[-1] if merged else None
        if prev and it['type'] == prev['type'] == 'text' and it['mono'] and prev['mono'] \
                and 0 < it['y'] - prev['last_y'] < prev['size'] * 2.2:
            prev['text'] += '\n' + it['text']  # 이어지는 코드 줄은 한 덩어리
            prev['last_y'] = it['y']
            continue
        if prev and it['type'] == prev['type'] == 'text' and not it['mono'] and not prev['mono'] \
                and abs(it['size'] - prev['size']) < 1 and not it['bullet']:
            same_line = abs(it['y'] - prev['y']) < 4
            follows = 0 < it['y'] - prev['last_y'] < prev['size'] * 1.6 and it['x'] >= prev['x'] - 2
            if same_line or follows:
                prev['text'] += ' ' + it['text']
                prev['last_y'] = it['y']
                continue
        it['last_y'] = it['y']
        merged.append(it)

    for f in figures:
        f['labels'] = _merge_labels(f['labels'])
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
    blocks = []
    for it in merged:
        if it['type'] == 'image':
            f = it['figure']
            blocks.append({'type': 'image', 'rect': [round(v, 1) for v in f['rect']],
                           'width': round(min(1.0, f['rect'].width / width), 3), 'raster': f['raster'] and not f['labels'],
                           'labels': f['labels']})
            continue
        if it is title:
            blocks.append({'type': 'heading', 'level': 0, 'text': it['text']})
            continue
        level = min(3, sum(1 for x in lefts if x < it['x'] - 8))
        kind = 'code' if it['mono'] else 'bullet' if it['bullet'] else 'text'
        blocks.append({'type': kind, 'level': level, 'text': it['text']})
    return blocks


def analyze_pdf(data: bytes) -> list[dict]:
    """[{page, text, blocks}] — text는 띄어쓰기를 되살린 페이지 전체 글 (챗봇·복습에 사용)"""
    pages = []
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        template = find_template(doc)
        for i, page in enumerate(doc):
            blocks = page_blocks(page, template)
            lines = []
            for b in blocks:
                if b['type'] == 'image':
                    lines += [label['text'] for label in b['labels']]
                else:
                    lines.append(b['text'])
            pages.append({'page': i + 1, 'text': '\n'.join(lines), 'blocks': blocks})
    return pages


def needs_translation(text: str) -> bool:
    """글자가 있는 것만 (주소·숫자만 있는 줄은 원문 유지)"""
    return bool(re.search(r'[^\W\d_]{2,}', URL.sub('', text)))


def units(blocks: list[dict]) -> list[tuple[int, int | None, str]]:
    """번역 단위 (블록 번호, 그림 글자 번호 또는 None, 글). 코드는 원문 유지"""
    out = []
    for i, b in enumerate(blocks):
        if b['type'] in ('heading', 'bullet', 'text') and needs_translation(b['text']):
            out.append((i, None, b['text']))
        elif b['type'] == 'image':
            out += [(i, j, label['text']) for j, label in enumerate(b['labels']) if needs_translation(label['text'])]
    return out


def marked_text(blocks: list[dict]) -> str:
    """번역 단위마다 ⟦B번호⟧ 표시를 붙여 한 번에 번역"""
    return '\n\n'.join(f'⟦B{n}⟧ {text}' for n, (_, _, text) in enumerate(units(blocks)))


def apply_marked(translated: str, blocks: list[dict]) -> bool:
    """번역 결과를 블록(translated)·그림 글자(labels[].translated)에 넣음. 표시가 어긋나면 False"""
    wanted = units(blocks)
    parts = MARK.split(translated)
    found = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if sorted(found) != list(range(len(wanted))) or not all(found.values()):
        return False
    for b in blocks:
        if b['type'] in ('heading', 'bullet', 'text'):
            b['translated'] = b['text']  # 번역 대상이 아니면 원문 그대로
        elif b['type'] == 'image':
            for label in b['labels']:
                label['translated'] = label['text']
    for n, (i, j, _) in enumerate(wanted):
        if j is None:
            blocks[i]['translated'] = found[n]
        else:
            blocks[i]['labels'][j]['translated'] = found[n]
    return True


def render_figure(data: bytes, page: int, rect: list[float], labels: list[dict] | None = None, dpi: int = 130) -> bytes:
    """그림 영역 PNG. labels에 번역이 있으면 원래 글자를 지우고 같은 자리에 번역문을 넣어 그림"""
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        p = doc[page - 1]
        todo = [label for label in labels or [] if label.get('translated') and label['translated'] != label['text']]
        for label in todo:
            p.add_redact_annot(pymupdf.Rect(label['rect']), fill=False)
        if todo:
            p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        for label in todo:
            r = pymupdf.Rect(label['rect'])
            box = pymupdf.Rect(r.x0 - 2, r.y0 - 1, r.x1 + 2, r.y1 + label['size'] * 0.4)
            css = (f"* {{font-family: sans-serif; font-size: {label['size']}px; color: #{label['color']:06x}; "
                   'line-height: 1.05; text-align: center;}}')
            p.insert_htmlbox(box, label['translated'], css=css, scale_low=0.3)
        return p.get_pixmap(clip=pymupdf.Rect(rect), dpi=dpi).tobytes('png')
