import pymupdf

MAX_CHUNK = 2500


class PDFPasswordError(ValueError):
    pass


def unlock_pdf(data: bytes, password: str | None = None) -> bytes:
    """열기 비밀번호를 확인하고, 필요하면 메모리에서 잠금을 푼 사본을 반환."""
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if not doc.needs_pass:
                return data
            if not password:
                raise PDFPasswordError("PDF_PASSWORD_REQUIRED")
            if not doc.authenticate(password):
                raise PDFPasswordError("PDF_PASSWORD_WRONG")
            return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE, no_new_id=True)
    except pymupdf.FileDataError as e:
        raise ValueError("PDF 파일을 열 수 없음") from e


def extract_pages(data: bytes) -> list[dict]:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return [{"page": i + 1, "text": p.get_text("text").strip()} for i, p in enumerate(doc)]


def split_chunks(text: str, limit: int = MAX_CHUNK) -> list[str]:
    """빈 줄 기준으로 나누되 limit 이하로 묶음"""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks
