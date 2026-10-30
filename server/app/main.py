import asyncio
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from .settings import load_environment

load_environment()

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import candidates as cand
from . import service
from .chat import ChatError, ChatRequest, ChatResponse, DocumentNotFound, answer_chat
from .learning import LearningStoreError, get_learning_store
from .llm import OpenAICompatible
from .pdf import extract_pages
from .notices import NoticeError, get_crawler
from .notice_extract import ExtractRequest, NoticeExtractionError, get_extractor

MAX_PDF = 100 * 1024 * 1024
COURSE_RE = re.compile(r"^[\w-]{1,64}$")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    crawler = get_crawler()
    crawler.start()
    try:
        yield
    finally:
        await asyncio.to_thread(crawler.stop)


app = FastAPI(title="LMS Helper Server", lifespan=lifespan)
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
        raise HTTPException(413, "PDF는 100MB 이하만 가능")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "PDF 파일이 아님")
    return data


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/notices")
def notices(
    source: Literal["school", "dept"],
    limit: int = Query(10, ge=1, le=30),
    page: int = Query(1, ge=1),
):
    try:
        return get_crawler().listing(source, limit, page)
    except NoticeError as e:
        raise HTTPException(503, str(e)) from e


@app.post("/notices/extract")
def extract_notices(request: ExtractRequest) -> dict:
    try:
        return {"items": get_extractor().extract_items(request.items)}
    except NoticeExtractionError as e:
        raise HTTPException(502, str(e)) from e


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


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        store = get_learning_store()
        response = answer_chat(request, llm)
        store.record_question(str(request.anon_id), request.doc_id, request.lang)
        return response
    except DocumentNotFound as e:
        raise HTTPException(404, "문서 없음") from e
    except ChatError as e:
        raise HTTPException(502, str(e)) from e
    except LearningStoreError as e:
        raise HTTPException(503, "학습 기록을 저장할 수 없음. 잠시 후 다시 시도하세요.") from e


@app.post("/glossary/candidates")
async def glossary_candidates(file: UploadFile = File(...), use_llm: bool = Form(True)):
    pages = extract_pages(await read_pdf(file))
    lists = [cand.regex_candidates(pages)]
    if use_llm:
        lists.insert(0, cand.llm_candidates(pages, llm()))
    return {"terms": cand.merge(*lists)}
