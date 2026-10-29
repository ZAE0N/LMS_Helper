import os
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import candidates as cand
from . import service
from .llm import OpenAICompatible
from .pdf import extract_pages

MAX_PDF = 100 * 1024 * 1024
COURSE_RE = re.compile(r"^[\w-]{1,64}$")

app = FastAPI(title="LMS Helper Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o],  # chrome-extension://<ID>
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
_llm = None


def llm():
    global _llm
    if _llm is None:
        _llm = OpenAICompatible()
    return _llm


async def read_pdf(f: UploadFile) -> bytes:
    data = await f.read(MAX_PDF + 1)
    if len(data) > MAX_PDF:
        raise HTTPException(413, "PDF는 20MB 이하만 가능")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "PDF 파일이 아님")
    return data


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/documents")
async def translate_document(file: UploadFile = File(...), lang: str = Form(...), course: str | None = Form(None)):
    if course and not COURSE_RE.match(course):
        raise HTTPException(400, "course 형식 오류")
    try:
        return service.translate_pdf(await read_pdf(file), lang, course, llm())
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


@app.get("/documents/{doc_id}")
def get_document(doc_id: str):
    if not re.fullmatch(r"[0-9a-f]{64}", doc_id) or (doc := service.get_document(doc_id)) is None:
        raise HTTPException(404, "문서 없음")
    return doc


@app.post("/glossary/candidates")
async def glossary_candidates(file: UploadFile = File(...), use_llm: bool = Form(True)):
    pages = extract_pages(await read_pdf(file))
    lists = [cand.regex_candidates(pages)]
    if use_llm:
        lists.insert(0, cand.llm_candidates(pages, llm()))
    return {"terms": cand.merge(*lists)}
