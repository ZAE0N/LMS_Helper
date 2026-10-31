import asyncio
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID
import httpx

from .settings import load_environment

load_environment()

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import candidates as cand
from . import service
from .chat import ChatError, ChatRequest, ChatResponse, DocumentNotFound, answer_chat
from .llm import OpenAICompatible
from .learning import LearningStoreError, QuizConflict, QuizForbidden, QuizNotFound, QuizSubmissionError, get_learning_store
from .grading import GradeError, GradeResponse, GradeShortRequest, QuizResultRequest, ResultResponse, grade_short
from .quiz import QuizDocumentNotFound, QuizError, QuizInputError, QuizRequest, QuizResponse, generate_quiz
from .report import ReportDocumentNotFound, ReportError, ReportResponse, get_report_builder
from .pdf import extract_pages
from .notices import NoticeError, get_crawler
from .notice_extract import ExtractRequest, get_extractor

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
    # 실패한 글은 summary 등이 null (전체를 오류로 만들지 않음)
    return {"items": get_extractor().extract_items(request.items)}


@app.post("/documents")
async def translate_document(file: UploadFile = File(...), lang: str = Form(...), course: str | None = Form(None)):
    if course and not COURSE_RE.match(course):
        raise HTTPException(400, "course 형식 오류")
    try:
        data = await read_pdf(file)
        return await asyncio.to_thread(service.translate_pdf, data, lang, course, llm())
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, "교안을 번역할 수 없음. 잠시 후 다시 시도하세요.") from e


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


@app.post("/quiz", response_model=QuizResponse)
def quiz(request: QuizRequest) -> QuizResponse:
    try:
        store = get_learning_store()
        response = generate_quiz(request, llm)
        store.save_quiz(str(response.quiz_id), str(request.anon_id), request.doc_id, request.lang,
                        [q.model_dump(mode='json') for q in response.questions])
        return response
    except QuizDocumentNotFound as e:
        raise HTTPException(404, "문서 없음") from e
    except QuizInputError as e:
        raise HTTPException(400, str(e)) from e
    except QuizError as e:
        raise HTTPException(502, str(e)) from e
    except LearningStoreError as e:
        raise HTTPException(503, "복습 문제를 저장할 수 없음. 잠시 후 다시 시도하세요.") from e


@app.post("/quiz/grade-short", response_model=GradeResponse)
def grade_short_answer(request: GradeShortRequest) -> GradeResponse:
    try:
        return grade_short(request, get_learning_store(), llm)
    except QuizNotFound as e:
        raise HTTPException(404, str(e)) from e
    except QuizSubmissionError as e:
        raise HTTPException(400, str(e)) from e
    except QuizConflict as e:
        raise HTTPException(409, str(e)) from e
    except GradeError as e:
        raise HTTPException(502, str(e)) from e
    except LearningStoreError as e:
        raise HTTPException(503, "채점 결과를 저장할 수 없음") from e


@app.post("/quiz/result", response_model=ResultResponse)
def quiz_result(request: QuizResultRequest) -> dict:
    try:
        return get_learning_store().submit_quiz_result(str(request.quiz_id), str(request.anon_id),
                                                       {str(answer.id): answer.correct for answer in request.answers})
    except QuizNotFound as e:
        raise HTTPException(404, str(e)) from e
    except QuizForbidden as e:
        raise HTTPException(403, str(e)) from e
    except QuizSubmissionError as e:
        raise HTTPException(400, str(e)) from e
    except QuizConflict as e:
        raise HTTPException(409, str(e)) from e
    except LearningStoreError as e:
        raise HTTPException(503, "복습 결과를 저장할 수 없음") from e


@app.get("/report", response_model=ReportResponse)
def report(anon_id: UUID, lang: Literal['en', 'ru'] = 'en',
           doc_id: str | None = Query(None, pattern=r'^[0-9a-f]{64}$')) -> ReportResponse:
    try:
        return get_report_builder(service.DATA_DIR / 'reports').build(str(anon_id), lang, doc_id, get_learning_store(), llm)
    except ReportDocumentNotFound as e:
        raise HTTPException(404, "문서 없음") from e
    except ReportError as e:
        raise HTTPException(502, str(e)) from e
    except LearningStoreError as e:
        raise HTTPException(503, "학습 기록을 읽을 수 없음") from e
    except (OSError, ValueError) as e:
        raise HTTPException(503, "리포트 데이터를 읽거나 저장할 수 없음") from e
