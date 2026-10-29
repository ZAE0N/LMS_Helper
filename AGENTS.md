# LMS Helper — 에이전트 규칙 (Claude Code · Codex 공통)

외국인 유학생이 인하공전 LMS(Canvas) 안에서 학교 공지와 교안을 모국어로 이해하도록 돕는 크롬 확장 프로그램 + 서버.
교내 공모전 MVP. **마감: 11/3 제출, 11/2까지 완성.**

## 1. 폴더 담당 (가장 중요)
| 폴더 | 담당 | 내용 |
|---|---|---|
| `extension/` | 정현 | 크롬 확장 (MV3, 빌드 도구 없는 순수 JS) |
| `server/` | 팀원 | FastAPI 서버 (번역, 공지 수집, 챗봇, 문제, 리포트) |
| `AGENTS.md`, `CLAUDE.md`, `docs/` | 공동 | 수정 시 두 사람 모두 동의 |

- **자기 담당 폴더만 수정한다.** 다른 폴더 수정이 필요하면 코드를 고치지 말고 `docs/requests.md`에 요청을 적는다.
- API 형식(아래 3장)을 바꿔야 하면 이 파일을 먼저 고치고 상대에게 알린 뒤 구현한다.

## 2. 구조
```
LMS 화면 ─ extension/content.js (로그인 세션으로 Canvas API 읽기, 테마 CSS)
            extension/sidepanel.* (공지 · 교안 뷰어 · 챗봇 · 복습 문제 · 리포트)
               ⇅ HTTP (JSON)
          server/ (LLM API 키는 서버에만)
```
- 개발 서버 주소: `http://localhost:8000` (확장은 `extension/config.js` 한 곳에서 관리, 바꾸면 `manifest.json`의 `host_permissions`도 함께)
- 사용자 구분: 로그인 없음. 확장 설치 시 만든 `anon_id`(UUID)만 사용
- 언어 코드: `mn vi uz ne zh en`

## 3. API 약속 (v1)
오류는 HTTP 4xx/5xx + `{"detail": "메시지"}`.

### 교안
- `POST /documents` — multipart: `file`(PDF), `lang`, `course`(선택, 용어집 파일명)
  → `{doc_id, lang, course, needs_review[], issue_pages[], pages: [{page, original, translated, terms[], issues[]}]}`
- `GET /documents/{doc_id}` → 위와 동일

### 공지
공통 객체 `Notice`:
```json
{
  "id": "canvas-123 | school-4295 | dept-111019",
  "source": "course | school | dept",
  "course": "과목명 또는 null",
  "title": "원문 제목", "title_tr": "번역 제목",
  "summary_tr": "한 줄 요약", "body_tr": "번역 본문",
  "deadline": "2026-11-03 또는 null",
  "deadline_text": "원문 속 날짜 표현 또는 null",
  "action_tr": "해야 할 일 또는 null",
  "posted_at": "ISO 8601", "url": "원문 링크"
}
```
- `POST /notices/translate` — 강의 공지(확장 프로그램이 Canvas에서 읽은 것)
  요청 `{lang, items: [{id, source: "course", course, title, body(평문), posted_at, updated_at, url}]}`
  → `{items: [Notice]}`
- `GET /notices?source=school|dept&lang=mn&limit=20` → `{items: [Notice]}` (최신순)

### 챗봇
- `POST /chat` 요청 `{anon_id, doc_id, lang, question, history: [{role, content}] (최근 6개)}`
  → `{answer, citations: [{page, quote}]}` — 교안에 근거가 없으면 그렇다고 답하고 `citations: []`

### 복습 문제
- `POST /quiz` 요청 `{anon_id, doc_id, lang, count: 5}`
  → `{quiz_id, questions: [{id, question, choices: [4개], answer_index, explanation, page}]}`
- `POST /quiz/result` 요청 `{anon_id, quiz_id, answers: [{id, choice_index}]}` → `{score, total}`

### 리포트 (간단)
- `GET /report?anon_id=...&lang=mn`
  → `{quizzes_taken, accuracy, questions_asked, weak_pages: [{doc_id, page, wrong}], summary_tr}`

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
- LMS 화면에 넣는 요소는 버튼 수준으로 최소화하고, 나머지는 사이드 패널에 둔다.

### 학교·학과 공지 수집
- 같은 게시판 확인은 30분에 한 번, 요청 간격 1초 이상. 새 글만 본문 요청.
- 시작 전 robots.txt 확인. User-Agent에 프로젝트 이름 표기.
- 대상 게시판 (두 곳 모두 같은 게시판 시스템, 직접 열어서 동작 확인 후 사용)
  - 학교 공지 목록: `https://www.inhatc.ac.kr/combBbs/kr/2/list.do?page=1`
  - 학과(컴퓨터정보공학과) 공지 목록: `https://cs.inhatc.ac.kr/bbs/cs/104/artclList.do`
  - 학과 상세 글: `https://cs.inhatc.ac.kr/bbs/cs/104/{글번호}/artclView.do`
  - 원래 주소(사람이 보는 페이지): 학교 `https://www.inhatc.ac.kr/kr/460/subview.do`, 학과 `https://cs.inhatc.ac.kr/cs/1754/subview.do`

### 번역
- 교안·공지 번역은 모두 `server/app/translate.py`의 용어집 파이프라인(`translate_text`)을 거친다.
- 마감일은 LLM이 뽑은 날짜가 원문에 실제로 있을 때만 채운다. 없으면 `null`.
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
