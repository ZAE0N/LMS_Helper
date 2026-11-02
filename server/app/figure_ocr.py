"""통째로 붙인 그림(사진·캡처) 속 한글을 OCR로 찾아 위치·색과 함께 돌려줌.
배경에서는 글자 둘레 색으로 덮고, 확장이 같은 자리에 번역문을 올린다.
RapidOCR(Apache-2.0) 한국어 모델은 처음 실행할 때 한 번 내려받음. FIGURE_OCR=0이면 끔"""
import logging
import os
import re
import statistics
from threading import Lock

import pymupdf

from .structure import _RENDER_LOCK, HANGUL, _r, label_box

log = logging.getLogger(__name__)
DPI = 300  # 화면 캡처 속 작은 글자도 읽도록 크게
SCAN_DPI = 100
MIN_SCORE = 0.5
GARBLED = re.compile(r'[\u3131-\u318e|{}\[\]<>]')  # 낱자모·기호가 섞이면 잘못 읽은 것
END = re.compile(r'[.!?]["”’\']?$')  # 문장 끝: 아래 줄과 합치지 않음
_lock = Lock()
_engine = None
_failed = False


def _get_engine():
    global _engine, _failed
    if _engine is None and not _failed:
        try:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
            # 한국어 인식 모델은 PP-OCRv5 mobile만 있음 (기본값 small은 지원 안 됨)
            _engine = RapidOCR(params={'Rec.lang_type': LangRec.KOREAN, 'Rec.ocr_version': OCRVersion.PPOCRV5,
                                       'Rec.model_type': ModelType.MOBILE})
        except Exception as e:  # 설치 안 됨 등: 그림은 원본 그대로
            _failed = True
            log.warning('그림 OCR 사용 불가: %s', type(e).__name__)
    return _engine


def _hex(rgb) -> str:
    return '#' + ''.join(f'{round(v):02x}' for v in rgb[:3])


def _colors(pix: pymupdf.Pixmap, box: tuple[int, int, int, int]) -> tuple[str, str]:
    """(배경색, 글자색). 배경 = 상자 둘레 색의 중앙값, 글자 = 상자 안에서 배경과 가장 다른 색들의 평균"""
    x0, y0, x1, y1 = (max(0, box[0] - 2), max(0, box[1] - 2), min(pix.width - 1, box[2] + 2), min(pix.height - 1, box[3] + 2))
    edge = [pix.pixel(x, y) for x in range(x0, x1 + 1, 2) for y in (y0, y1)] + \
           [pix.pixel(x, y) for y in range(y0, y1 + 1, 2) for x in (x0, x1)]
    bg = [statistics.median(c[i] for c in edge) for i in range(3)]
    inside = [pix.pixel(x, y) for x in range(box[0], box[2], 2) for y in range(box[1], box[3], 2)] or edge
    far = sorted(inside, key=lambda c: -sum((c[i] - bg[i]) ** 2 for i in range(3)))[:max(1, len(inside) // 7)]
    fg = [sum(c[i] for c in far) / len(far) for i in range(3)]
    return _hex(bg), _hex(fg)


def figure_labels(data: bytes, page: int, figures: list[dict], known: list[list[float]]) -> list[dict]:
    """raster 그림마다 한글 글자를 찾아 label 블록으로. known: 이미 PDF 글자로 있는 위치(겹치면 제외)"""
    rasters = [pymupdf.Rect(f['rect']) for f in figures if f.get('raster')]
    if not rasters or os.getenv('FIGURE_OCR', '1') == '0' or _get_engine() is None:
        return []
    with _RENDER_LOCK, pymupdf.open(stream=data, filetype='pdf') as doc:
        area = doc[page - 1].rect
        # 그림만 찍은 것과 둘레를 조금 넓혀 찍은 것(가장자리에 붙은 글자) 둘 다 읽고, 겹치면 점수가 높은 쪽
        clips = rasters + [r + (-8, -8, 8, 8) & area for r in rasters]
        shots = [(clip, doc[page - 1].get_pixmap(clip=clip, dpi=DPI)) for clip in clips]
        strips = [(r, doc[page - 1].get_pixmap(clip=r, dpi=DPI)) for r in _strips(doc[page - 1], rasters)]
        whole = doc[page - 1].get_pixmap(dpi=SCAN_DPI)  # 번역문 자리(칸) 찾기용
    global _failed
    labels = []
    for clip, pix in shots:
        try:
            with _lock:
                # 방향 판별(cls)은 바른 줄을 거꾸로 보고 뒤집어 읽는 일이 많아 끔 (슬라이드 그림은 거꾸로 붙지 않음).
                # 아래 낱말 읽기가 설정을 바꿔 두므로 매번 지정
                out = _engine(pix.tobytes('png'), use_det=True, use_cls=False, use_rec=True)
        except Exception as e:  # 모델을 받지 못한 경우 등
            _failed = True
            log.warning('그림 OCR 실패: %s', type(e).__name__)
            return []
        scale = 72 / DPI
        for box, text, score in zip(out.boxes if out.boxes is not None else [], out.txts or [], out.scores or []):
            text = str(text).strip()
            if score < MIN_SCORE or not HANGUL.search(text) or GARBLED.search(text):
                continue
            xs, ys = [int(p[0]) for p in box], [int(p[1]) for p in box]
            px = (min(xs), min(ys), max(xs), max(ys))
            rect = pymupdf.Rect(clip.x0 + px[0] * scale, clip.y0 + px[1] * scale, clip.x0 + px[2] * scale, clip.y0 + px[3] * scale)
            if any((rect & pymupdf.Rect(k)).get_area() > 0.5 * rect.get_area() for k in known):
                continue
            fill, color = _colors(pix, px)
            size = round(rect.height * 0.72, 1)
            left, right = _edges(pix, px, fill)
            cover = pymupdf.Rect(clip.x0 + left * scale, rect.y0 - 1, clip.x0 + right * scale, rect.y1 + 1) | rect + (-1, -1, 1, 1)
            labels.append({'type': 'label', 'level': 0, 'text': text, 'rect': _r(rect), 'size': size, 'color': color,
                           'bold': False, 'align': 'center', 'box': _r(label_box(rect, size)), 'ocr': True,
                           'fill': fill, 'lines': [_r(cover)], 'score': score})
    # 낱말마다 따로 붙인 작은 글자 그림("강의" "계획" "안내"): 위치 찾기 없이 한 줄로 바로 읽음
    for rect, pix in strips:
        try:
            with _lock:
                out = _engine(pix.tobytes('png'), use_det=False, use_cls=False, use_rec=True)
        except Exception:
            continue
        for text, score in zip(out.txts or [], out.scores or []):
            text = str(text).strip()
            if score < 0.8 or not HANGUL.search(text) or GARBLED.search(text) or \
                    any((rect & pymupdf.Rect(k)).get_area() > 0.5 * rect.get_area() for k in known):
                continue
            fill, color = _colors(pix, (0, 0, pix.width - 1, pix.height - 1))
            size = round(rect.height * 0.62, 1)
            labels.append({'type': 'label', 'level': 0, 'text': text, 'rect': _r(rect), 'size': size, 'color': color,
                           'bold': False, 'align': 'center', 'box': _r(label_box(rect, size)), 'ocr': True,
                           'fill': fill, 'lines': [_r(rect)], 'score': score + 0.05})  # 겹치면 이쪽을 남김
    labels.sort(key=lambda x: -x['score'])
    out = [{k: v for k, v in x.items() if k != 'score'} for x in _stack(labels)]
    for label in out:
        frame = next((r + (-8, -8, 8, 8) for r in rasters if (r + (-8, -8, 8, 8)).contains(pymupdf.Rect(label['rect']))), None)
        if frame is not None:
            start = pymupdf.Rect(label['rect'])
            for line in label['lines']:  # 지운 자리(따옴표 등 포함)부터 넓힘
                start |= pymupdf.Rect(line) + (1, 1, -1, -1)
            label['box'] = _r(_room(whole, start, frame, label['fill'], [pymupdf.Rect(o['rect']) for o in out if o is not label]))
            label['wrap'] = True
    _split(out)
    return out


def _split(labels: list[dict]) -> None:
    """넓힌 자리끼리 겹치면 두 글자 사이 가운데에서 나눔 (원래 글자 자리보다 작게는 줄이지 않음)"""
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            if not (pymupdf.Rect(a['box']) & pymupdf.Rect(b['box'])).get_area():
                continue
            ra, rb = a['rect'], b['rect']
            gx = max(rb[0] - ra[2], ra[0] - rb[2])
            gy = max(rb[1] - ra[3], ra[1] - rb[3])
            if gx < 0 and gy < 0:  # 글자끼리 겹침: 나눌 수 없음
                continue
            k = 0 if gx >= gy else 1  # 옆으로 나란하면 세로선, 위아래면 가로선으로 나눔
            lo, hi = (a, b) if ra[k] < rb[k] else (b, a)
            mid = (lo['rect'][k + 2] + hi['rect'][k]) / 2
            lo['box'][k + 2] = round(min(lo['box'][k + 2], max(mid, lo['rect'][k + 2])), 1)
            hi['box'][k] = round(max(hi['box'][k], min(mid, hi['rect'][k])), 1)


def _edges(pix: pymupdf.Pixmap, px: tuple[int, int, int, int], fill: str) -> tuple[int, int]:
    """OCR 상자 밖에 붙은 따옴표 등 작은 표시까지 지우도록 좌우로 넓힌 범위(픽셀).
    글자 높이의 35%까지, 세로선·다른 글자처럼 줄 높이 절반 넘게 칠해진 세로줄을 만나면 멈춤"""
    x0, y0, x1, y1 = px
    bg = [int(fill[i:i + 2], 16) for i in (1, 3, 5)]
    data, w, n = pix.samples, pix.width, pix.n
    ys = range(max(0, y0), min(pix.height, y1 + 1))

    def light(x: int) -> bool:
        ink = sum(sum((data[(y * w + x) * n + c] - bg[c]) ** 2 for c in range(3)) >= 400 for y in ys)  # 연한 칸선도 잡음
        return ink <= 0.5 * len(ys)

    reach = int((y1 - y0) * 0.35)
    lo, hi = max(0, x0 - reach), min(w - 1, x1 + reach)
    left, right = x0, x1
    while left > lo and light(left - 1):
        left -= 1
    while right < hi and light(right + 1):
        right += 1
    # 선을 만나 멈췄으면 번진 가장자리까지 지우지 않도록 조금 띄움
    return min(x0, left + 3) if left > lo else left, max(x1, right - 3) if right < hi else right


def _room(pix: pymupdf.Pixmap, r: pymupdf.Rect, frame: pymupdf.Rect, fill: str, others: list[pymupdf.Rect]) -> pymupdf.Rect:
    """그림 속 글자 둘레의 빈 바탕(표의 한 칸)까지 번역문 자리를 넓힘. 선·다른 글자·그림 경계를 만나면 멈춤"""
    k = SCAN_DPI / 72
    bg = [int(fill[i:i + 2], 16) for i in (1, 3, 5)]
    data, w, n = pix.samples, pix.width, pix.n

    def plain(x: int, y: int) -> bool:
        i = (y * w + x) * n
        return sum((data[i + c] - bg[c]) ** 2 for c in range(3)) < 400  # 연한 칸선(바탕과 17~23 차이)도 선으로

    def column(x: float, y0: float, y1: float) -> bool:
        ys = range(int(y0 * k), max(int(y0 * k) + 1, int(y1 * k)))
        return sum(plain(int(x * k), y) for y in ys) >= 0.9 * len(ys)

    def row(y: float, x0: float, x1: float) -> bool:
        xs = range(int(x0 * k), max(int(x0 * k) + 1, int(x1 * k)))
        return sum(plain(x, int(y * k)) for x in xs) >= 0.9 * len(xs)

    step, h = 1 / k, r.height
    ty0, ty1 = r.y0 + h * 0.15, r.y1 - h * 0.15
    blocked = lambda q: any(q.intersects(o) for o in others)  # noqa: E731
    x0 = r.x0
    while x0 - step > max(frame.x0, r.x0 - 400) and column(x0 - step, ty0, ty1) and not blocked(pymupdf.Rect(x0 - step - 2, ty0, x0, ty1)):
        x0 -= step
    x1 = r.x1
    while x1 + step < min(frame.x1, r.x1 + 400) and column(x1 + step, ty0, ty1) and not blocked(pymupdf.Rect(x1, ty0, x1 + step + 2, ty1)):
        x1 += step
    y0 = r.y0
    while y0 - step > max(frame.y0, r.y0 - h * 1.5) and row(y0 - step, r.x0, r.x1) and not blocked(pymupdf.Rect(r.x0, y0 - step - 2, r.x1, y0)):
        y0 -= step
    y1 = r.y1
    while y1 + step < min(frame.y1, r.y1 + h * 1.5) and row(y1 + step, r.x0, r.x1) and not blocked(pymupdf.Rect(r.x0, y1, r.x1, y1 + step + 2)):
        y1 += step
    return pymupdf.Rect(x0 + 1, y0 + 1, x1 - 1, y1 - 1) | r


def _strips(page: pymupdf.Page, rasters: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """그림 영역 안에서 가로로 이어 붙은 낮고 긴 작은 그림들(낱말 그림)을 한 줄로 묶음"""
    words = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info['bbox'])
        if 12 <= r.height <= 60 and r.width >= r.height * 1.2 and r.width < 400 and any((f + (-4, -4, 4, 4)).contains(r) for f in rasters):
            words.append(r)
    lines: list[pymupdf.Rect] = []
    for r in sorted(words, key=lambda x: (round(x.y0 / 4), x.x0)):
        k = next((i for i, x in enumerate(lines) if abs(x.y0 - r.y0) < 3 and abs(x.y1 - r.y1) < 3
                  and max(r.x0 - x.x1, x.x0 - r.x1) < r.height * 0.6), None)
        if k is None:
            lines.append(pymupdf.Rect(r))
        else:
            lines[k] = lines[k] | r
    return lines


def _stack(labels: list[dict]) -> list[dict]:
    """위아래로 붙은 가운데 정렬 두 줄("데이터 분석과" + "접근법")은 한 글로 번역. 그림이 달라도 합침.
    윗줄이 문장으로 끝나면(따옴표로 묶인 예문 여러 줄 등) 줄마다 따로"""
    kept: list[dict] = []
    for label in labels:  # 앞에 있는 것(점수가 높은 것)을 남김
        r = pymupdf.Rect(label['rect'])
        if not any((r & pymupdf.Rect(o['rect'])).get_area() > 0.5 * min(r.get_area(), pymupdf.Rect(o['rect']).get_area()) for o in kept):
            kept.append(label)  # 같은 글을 두 번 읽은 경우는 하나만
    out: list[dict] = []
    for label in sorted(kept, key=lambda x: (x['rect'][1], x['rect'][0])):
        r = pymupdf.Rect(label['rect'])
        prev = next((o for o in reversed(out) if 0.7 < o['size'] / label['size'] < 1.45 and not END.search(o['text'])
                     and -0.3 * label['size'] <= r.y0 - o['rect'][3] < 0.6 * label['size']
                     and abs((r.x0 + r.x1) - (o['rect'][0] + o['rect'][2])) / 2 < max(r.width, o['rect'][2] - o['rect'][0]) / 2), None)
        if prev is None:
            out.append(dict(label))
            continue
        union = pymupdf.Rect(prev['rect']) | r
        prev.update(text=f"{prev['text']} {label['text']}", rect=_r(union), lines=prev['lines'] + label['lines'],
                    box=_r(label_box(union, prev['size'])), wrap=True)
    return out
