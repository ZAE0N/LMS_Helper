import pytest

from app import main
from app.layout_pdf import LayoutPdf


@pytest.fixture(autouse=True)
def no_real_pdf2zh(monkeypatch):
    # .env에 PDF2ZH_BIN이 있어도 테스트에서 실제 pdf2zh(토큰 사용)를 실행하지 않게 기본으로 끔
    monkeypatch.setattr(main, 'get_layout', lambda: LayoutPdf(None))
