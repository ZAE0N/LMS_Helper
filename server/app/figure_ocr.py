"""통째로 붙인 그림(사진·캡처) 속 한글을 OCR로 찾아 위치·색과 함께 돌려줌.
배경에서는 글자 둘레 색으로 덮고, 확장이 같은 자리에 번역문을 올린다.
RapidOCR(Apache-2.0) 한국어 모델은 처음 실행할 때 한 번 내려받음. FIGURE_OCR=0이면 끔"""
import logging
import os
import statistics
from threading import Lock

import pymupdf

from .structure import _RENDER_LOCK, HANGUL, _r, label_box

log = logging.getLogger(__name__)
DPI = 200
MIN_SCORE = 0.6
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
        shots = [(clip, doc[page - 1].get_pixmap(clip=clip, dpi=DPI)) for clip in rasters]
    global _failed
    labels = []
    for clip, pix in shots:
        try:
            with _lock:
                out = _engine(pix.tobytes('png'))
        except Exception as e:  # 모델을 받지 못한 경우 등
            _failed = True
            log.warning('그림 OCR 실패: %s', type(e).__name__)
            return []
        scale = 72 / DPI
        for box, text, score in zip(out.boxes if out.boxes is not None else [], out.txts or [], out.scores or []):
            text = str(text).strip()
            if score < MIN_SCORE or not HANGUL.search(text):
                continue
            xs, ys = [int(p[0]) for p in box], [int(p[1]) for p in box]
            px = (min(xs), min(ys), max(xs), max(ys))
            rect = pymupdf.Rect(clip.x0 + px[0] * scale, clip.y0 + px[1] * scale, clip.x0 + px[2] * scale, clip.y0 + px[3] * scale)
            if any((rect & pymupdf.Rect(k)).get_area() > 0.5 * rect.get_area() for k in known):
                continue
            fill, color = _colors(pix, px)
            size = round(rect.height * 0.72, 1)
            labels.append({'type': 'label', 'level': 0, 'text': text, 'rect': _r(rect), 'size': size, 'color': color,
                           'bold': False, 'align': 'center', 'box': _r(label_box(rect, size)), 'ocr': True,
                           'fill': fill, 'lines': [_r(rect + (-1, -1, 1, 1))]})
    return labels
