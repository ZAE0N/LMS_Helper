import pymupdf

MAX_CHUNK = 2500


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
