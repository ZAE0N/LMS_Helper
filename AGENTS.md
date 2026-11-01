# LMS Helper — 에이전트 규칙 (Claude Code · Codex 공통)

외국인 유학생이 인하공전 LMS(Canvas) 안에서 학교 공지와 교안을 모국어로 이해하도록 돕는 크롬 확장 프로그램 + 서버.

## 1. 폴더 담당 (가장 중요)
| 폴더 | 담당 | 내용 |
|---|---|---|
| `extension/` | 전정현 | 크롬 확장 (MV3, 빌드 도구 없는 순수 JS) |
| `server/` | 카로프 파루흐 | FastAPI 서버 (번역, 공지 수집, 챗봇, 문제, 리포트) |
| `AGENTS.md`, `CLAUDE.md`, `docs/` | 공동 | 수정 시 두 사람 모두 동의 |

- **자기 담당 폴더만 수정.** 다른 폴더 수정이 필요하면 코드를 고치지 말고 `docs/requests.md`에 요청을 적는다.
- API 형식(아래 3장)을 바꿔야 하면 이 파일을 먼저 고치고 상대에게 알린 뒤 구현한다.

## 2. 구조
```
LMS 페이지 ─ extension/content.js  (로그인 세션으로 Canvas API 읽기, 크롬 번역 감지, 상단 안내, 테마)
            extension/widgets.*   (오른쪽 서랍: 알림 · 교안(과목별) · 복습. 한국어 → 크롬 번역)
               │  서버 요청은 background.js가 대신 보냄 (CORS)
확장 화면  ─ extension/viewer.*    (교안 뷰어 + 챗봇, 복습 문제, 리포트. 영어·러시아어 i18n.js)
            extension/sidepanel.* (교안 언어 설정, 테마, 개발용 진단)
               ⇅ HTTP (JSON)
          server/ (LLM API 키는 서버에만)
```
- 개발 서버 주소: `http://localhost:8000` (확장은 `extension/config.js` 한 곳에서 관리, 바꾸면 `manifest.json`의 `host_permissions`도 함께)
- 사용자 구분: 로그인 없음. 확장 설치 시 만든 `anon_id`(UUID)만 사용
- 언어 코드: `en` `ru` (필요 시 `ja`)
- 코드에 `localhost` 등 서버 주소를 쓰지 않는다. 확장은 `CONFIG.SERVER_URL`, 서버는 환경변수만 사용

## 3. API 약속
오류는 HTTP 4xx/5xx + `{"detail": "메시지"}`.
언어 코드: `en`(영어) · `ru`(러시아어) · `ja`(일본어, 필요 시 추가. MVP는 영어·러시아어만)

### 교안
- `POST /documents` — multipart: `file`(PDF, **업로드 1회 최대 100MB**), `lang`, `course`(선택, 용어집 파일명)
  → `{doc_id, lang, course, needs_review[], issue_pages[], pages: [{page, original, translated, terms[], issues[], size, image, background?, blocks[]}]}`
  - `original`은 띄어쓰기를 되살린 페이지 글 (챗봇·복습에 사용), `translated`는 페이지 전체 번역
  - **슬라이드 보기**: 확장은 `background`(번역할 글자를 지운 페이지 그림) 위 같은 자리에 블록의 `translated`를 올려 원본 모양을 유지. `size`: `[폭, 높이]`(pt), `image`: 원본 페이지 그림
  - `blocks`: `[{type, level: 0~3, text, translated?, rect, box, size, color, bold, align?, wrap?, ocr?}]` (좌표는 pt, `[x0, y0, x1, y1]`)
    - `type`: `heading` · `bullet` · `text` · `code` · `label`(그림 속 글자) · `comment`(코드 속 한글 주석)
    - `rect`: 원문 위치, `box`: 번역문이 들어갈 자리(번역문이 길면 확장이 글자를 줄임), `align`: `center`·`left`(없으면 왼쪽), `wrap: false`인 그림 속 글자는 한 줄로(넘치면 글자를 줄임)
    - 페이지마다 한 번에 용어집 파이프라인으로 번역. 덩어리별로 맞지 않으면 블록에 `translated`가 없고 `background`도 없음 → 확장은 원본 페이지 + 페이지 전체 번역
    - 주소·숫자만 있는 줄, 번역해도 같은 글은 원문 그대로(지우지 않음). 코드는 번역하지 않고 한글 주석만 `comment`로 번역
    - 통째로 붙인 그림(사진·캡처) 속 한글은 OCR로 찾아 `label`(`ocr: true`)로 번역하고, 배경에서는 그 자리를 둘레 색으로 덮음 (OCR이 안 되면 그림은 원본)
    - 머리·꼬리 장식(로고, 학교명, 과목명)은 번역하지 않고 그대로 둠. 여러 페이지에 반복되는 단원 제목은 번역
- `GET /documents/{doc_id}` → 위와 동일
- `GET /documents/{doc_id}/figures/{name}` → 페이지 그림 (`{page}.jpg` 원본, `{page}-bg.jpg` 배경, JPEG)
- `GET /documents/{doc_id}/pdf` → 모양을 유지한 번역 PDF (베타, 확장에서는 숨김). 아직 만드는 중이면 `202 {"status": "processing"}`, 기능 꺼짐 503, 원본 없음 404
  - 처음 요청할 때 만들기 시작하고, 교안마다 한 번만 만들어 저장해 모두 같은 파일을 받는다 (매번 다시 번역하지 않음)

### 공지 (v3: 번역 없음, 한국어 그대로. 번역은 크롬 번역이 담당)
LMS 페이지 오른쪽 서랍의 알림 탭: **수업**(LMS 강의 공지) · **학교** · **학과**

공통 객체 `Notice`:
```json
{
  "id": "canvas-123 | school-4295 | dept-111019",
  "source": "course | school | dept",
  "course": "과목명 또는 null",
  "title": "제목", "summary": "한 줄 요약 또는 null(추출 실패)", "body": "본문 평문",
  "deadline": "2026-11-03 또는 null",
  "deadline_text": "원문 속 날짜 표현 또는 null",
  "action": "해야 할 일 (한국어) 또는 null",
  "posted_at": "ISO 8601", "url": "원문 링크"
}
```
- `GET /notices?source=school|dept&limit=10&page=1` — 학교·학과 탭용 (`lang` 없음)
  - `limit`: 기본 10, 최대 30 (화면에서 10 / 20 / 30 선택)
  - `page`: "더 보기"용, 1부터
  - 수집할 때 `summary`·`deadline`·`action`을 함께 추출해 저장. 추출이 실패해도 목록은 그대로 제공 (값만 `null`)
  → `{items: [Notice], page, has_more}` (최신순)
- `POST /notices/extract` — 수업 탭용. 확장 프로그램이 Canvas에서 읽고 **HTML 태그를 제거한 평문**으로 보냄. 한 번에 최대 30건
  요청 `{items: [{id, title, body, posted_at}]}` (`posted_at` 선택, 연도 없는 날짜의 연도 계산용)
  → `{items: [{id, summary, deadline, deadline_text, action}]}`
  - `deadline`은 원문에 실제로 있는 날짜일 때만, 없으면 `null`
  - 일부 글의 추출이 실패해도 요청 전체를 오류로 만들지 않는다. 그 글만 네 값이 모두 `null` (저장하지 않고 다음에 다시 추출)
- `POST /notices/translate`는 사용하지 않음 (삭제)

### 챗봇
- 대화 기록은 확장 프로그램이 교안별로 저장 (`summary` + 그 이후 `history`)
- `POST /chat`
  요청 `{anon_id, doc_id, lang, question, summary: "이전 대화 요약 또는 null", history: [{role, content}]}`
  → `{answer, citations: [{page, quote}], summary: "새 요약 또는 null", kept: 숫자 또는 null}`
- 대화 압축 규칙: `history`가 **16개(질문·답변 8쌍)를 넘으면** 서버가 오래된 메시지와 기존 요약을 합쳐 새 요약 1개로 압축하고, 최근 8개만 남김
  - 응답의 `summary`가 null이 아니면 확장 프로그램은 저장된 요약을 교체하고 `history`를 최근 `kept`개만 남긴다
- 교안에 근거가 없으면 그렇다고 답하고 `citations: []`

### 복습 문제
- 문제 유형 (사용자가 선택): `multiple` 객관식 · `short` 주관식 · `matching` 연결형
- `POST /quiz`
  요청 `{anon_id, doc_id, lang, count: 1~20 (기본 5), types: ["multiple", "short", "matching"] 중 선택, pages: [시작, 끝] 또는 null}`
  → `{quiz_id, questions: [Question]}`
- `Question` 공통: `{id, type, question, explanation, page}` + 유형별
  - `multiple`: `choices` (4개), `answer_index` (0부터)
  - `short`: `model_answer`, `keywords[]`
  - `matching`: `pairs: [[왼쪽, 정답 오른쪽], ...]` (오른쪽 순서는 확장 프로그램이 섞어서 표시)
- 채점: `multiple`·`matching`은 확장 프로그램이 직접 채점, `short`만 서버 채점
- `POST /quiz/grade-short` 요청 `{lang, quiz_id, question_id, user_answer}` → `{is_correct, score: 0~100, reason_tr}`
- `POST /quiz/result` 요청 `{anon_id, quiz_id, answers: [{id, correct: true/false}]}` → `{score, total}`

### 리포트
- `GET /report?anon_id=...&lang=en&doc_id=...` (`doc_id` 생략 시 전체)
  → 응답 예시:
  ```json
  {
    "quizzes_taken": 3, "accuracy": 0.72,
    "accuracy_by_type": {"multiple": 0.8, "short": 0.5, "matching": 0.75},
    "questions_asked": 12,
    "weak_pages": [{"doc_id": "...", "page": 7, "wrong": 3}],
    "exam_points": [{"point_tr": "시험 예상 포인트", "why_tr": "근거 설명", "pages": [3, 7]}],
    "summary_tr": "전체 학습 요약"
  }
- `exam_points` 규칙: 교안 내용에만 근거 (반복 강조된 개념, 정의·비교·절차, 학생이 자주 틀린 부분), 근거 페이지 필수, 화면에는 **"시험 예상"**으로 표시 (확정 아님)

## 4. 반드시 지킬 것
### 보안·개인정보
- API 키, 토큰, `.env`는 절대 커밋하지 않는다. 코드에 하드코딩 금지.
- 확장 프로그램은 Canvas 토큰을 만들거나 저장하지 않는다 (로그인 세션만 사용).
- 교안 서명 다운로드 URL은 서버로 보내지 않는다. 파일 내용만 보낸다.
- 로그에 이름·학번·공지 본문 전체를 남기지 않는다.
- 교안 PDF, 번역 결과(`data/`)는 커밋하지 않는다 (저작권).

### LMS 화면
- 테마 CSS는 색·모서리·그림자·글꼴만 바꾼다. `display`, `position`, `width` 등 레이아웃 변경 금지.
- 퀴즈·시험 경로(`/quizzes`, `/quiz_submissions`, `/assessments`, `/take`)에서는 테마와 화면 주입을 끈다.
- LMS 화면 전체 번역은 하지 않는다. 메뉴·버튼 등 기본 화면은 브라우저(크롬) 번역에 맡기고, 우리는 공지·교안·챗봇·복습만 담당한다.
- LMS 페이지에 넣는 우리 요소는 오른쪽 서랍(손잡이 포함)과 상단 안내만. 기존 LMS 요소는 수정·삭제하지 않고 덧붙이기만 한다.
- 우리가 AI로 번역해 넣은 결과(교안 등)에는 `translate="no"`를 붙여 크롬 번역이 덮어쓰지 않게 한다. 한국어 문구(서랍 등)는 크롬 번역이 번역하도록 그대로 둔다.

### 학교·학과 공지 수집
- 같은 게시판 확인은 정상 수집 후 30분에 한 번, 실패 시 5분 후 재시도. 실패 중에도 기존 캐시는 계속 제공. 요청 간격 1초 이상. 새 글만 본문 요청.
- 시작 전 robots.txt 확인. User-Agent에 프로젝트 이름 표기.
- 대상 게시판 (두 곳 모두 같은 게시판 시스템, 직접 열어서 동작 확인 후 사용)
  - 학교 공지 목록: `https://www.inhatc.ac.kr/combBbs/kr/2/list.do?page=1`
  - 학과(컴퓨터정보공학과) 공지 목록: `https://cs.inhatc.ac.kr/bbs/cs/104/artclList.do`
  - 학과 상세 글: `https://cs.inhatc.ac.kr/bbs/cs/104/{글번호}/artclView.do`
  - 원래 주소(사람이 보는 페이지): 학교 `https://www.inhatc.ac.kr/kr/460/subview.do`, 학과 `https://cs.inhatc.ac.kr/cs/1754/subview.do`

### 번역
- AI 번역은 교안에만 쓰고, 반드시 `server/app/translate.py`의 용어집 파이프라인(`translate_text`)을 거친다. 공지는 번역하지 않는다 (한국어 그대로, 크롬 번역 담당).
- 공지의 마감일·할 일은 한국어로 추출한다. 마감일은 LLM이 뽑은 날짜가 원문에 실제로 있을 때만 채운다. 없으면 `null`. 원문에 월·일만 있으면 연도는 게시일 기준(게시일보다 90일 넘게 이전이면 다음 해).
- 챗봇·문제는 교안 내용에만 근거하고 출처 페이지를 반드시 붙인다.

## 5. 코드 작성 규칙
- 주석은 꼭 필요한 것만, 짧게.
- 서버: 타입 힌트, 새 기능마다 pytest 추가. 테스트에서는 실제 LLM을 부르지 말고 가짜 LLM 사용. 커밋 전 `python -m pytest -q` 통과.
- 확장: 빌드 도구 없이 순수 JS. 화면 문구는 한 곳(객체)에 모아 언어별로 관리.
- 새 라이브러리 추가 시 `requirements.txt`(버전 고정) 또는 README에 기록.
- 큰 변경 전에는 무엇을 어떻게 바꿀지 먼저 설명하고 진행한다.

## 6. Git 규칙
- 저장소는 **비공개(private)**. 브랜치: `main`(항상 실행 가능) + 작업 브랜치.
- 작업 브랜치 이름: `ext/기능명`(정현), `server/기능명`(팀원). 예: `ext/notice-tab`, `server/notice-crawler`
- 작업 시작 전: `git switch main && git pull` 후 브랜치 생성.
- 커밋 메시지: `종류(범위): 한 줄 설명`
  - 종류: `feat` 기능 · `fix` 버그 · `refactor` 구조 변경 · `test` 테스트 · `docs` 문서 · `chore` 설정
  - 범위: `ext` · `server` · `docs`
  - 예: `feat(server): 학과 공지 수집기 추가`, `fix(ext): 퀴즈 페이지에서 테마가 켜지는 문제 수정`
- 커밋은 작게, 하나의 변경만. 작동하지 않는 코드는 `main`에 합치지 않는다.
- 푸시: 작업을 멈출 때마다 작업 브랜치를 푸시.
- 합치기: PR을 만들어 `main`에 합친다. 자기 폴더만 바뀐 PR은 본인이 바로 합쳐도 된다. `AGENTS.md`·API 변경 PR은 상대 확인 후 합친다.
- 매일 밤 10시: 각자 `main`에 합치고, 둘 다 `main`을 받아 확장+서버를 함께 실행해 본다.
- 금지: `main`에 force push, 공유 브랜치에서 `git reset --hard`, 남의 커밋 되돌리기.
