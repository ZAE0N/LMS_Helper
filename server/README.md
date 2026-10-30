# LMS Helper 서버 — 교안 번역 (용어집 적용)

## 처리 순서 (페이지마다)
1. PDF에서 페이지별 텍스트 추출 (긴 페이지는 2,500자 단위로 분할)
2. 코드 보호: 코드 줄·`PreparedStatement`·`get_user()`·URL 등을 `⟦C1⟧`로 숨김
3. 그 페이지에 나온 용어만 골라 프롬프트에 넣음
4. LLM 번역
5. 자동 검사: 자리표시 유지 / KEEP 그대로 / FIXED 번역어 사용 / BILINGUAL 원문 병기 / 한국어 잔존
6. 위반 시 위반 목록을 알려 주고 1회 재번역 → 그래도 남으면 `issues`에 기록
7. 코드 복원 후 저장 (같은 내용·언어·용어집이면 캐시 재사용)

## 용어집 (`glossary/`)
- `common.json`: 학사·과제 공통 용어. 모든 과목에 적용
- `<과목>.json`: 과목 용어. 공통과 겹치면 과목 쪽이 우선

### 규칙 (`rule` 값)
| 값 | 우리말 | 의미 | 예시 (en) | 대상 |
|---|---|---|---|---|
| `keep` | 유지 | 번역하지 않고 원문 그대로 | `SQL Injection` → `SQL Injection` | 영어 전문용어·약어·코드·명령어 |
| `fixed` | 고정 번역 | 용어집에 정한 번역어로 항상 똑같이 | `과제` → 항상 `assignment` | 자주 나오는 일반 학사 용어 |
| `bilingual` | 원문 병기 | 번역어 뒤에 한국어 원문을 괄호로 | `입력값 검증` → `input validation (입력값 검증)` | 시험·수업·학교 서류에 한국어로 나오는 핵심 개념 |

- `fixed`인데 해당 언어 번역어가 비어 있으면(`"ru": ""`) 자동으로 `bilingual`로 처리하고 `needs_review`에 표시. 채우면 다음부터 `fixed`로 동작
- 번역어 칸에 예시 문구를 넣지 말 것 (그 문구가 실제 번역어로 쓰임)
- 한국어 용어는 단독 또는 조사가 붙은 경우만 인식 ("인가"는 "무엇인가"에 걸리지 않음)
- 지원 언어 코드: `en ru` (필요 시 `ja`) (`app/translate.py`의 `LANGS`)

### 새 과목 용어집 만들기
```bash
python -m app.cli candidates 교안.pdf > glossary/data-analysis.json   # LLM 없이: --no-llm
```
팀원이 열어서 규칙을 고치고 `translations`에 언어별 번역어를 채움

## 실행
```bash
pip install -r requirements.txt
cp .env.example .env        # 값 채우기
uvicorn app.main:app --port 8000
```
- `.env`는 서버와 CLI가 `server/.env`에서 자동으로 읽음. 이미 설정된 환경변수가 우선.
- macOS 최초 설정 (저장소 루트): `python3 -m venv server/.venv`, `source server/.venv/bin/activate`, `cd server`, `pip install -r requirements.txt`.
- `.env`의 `LLM_API_KEY`를 채우고 `LLM_MODEL=gpt-4o-mini` 설정. 키를 채팅·커밋·캡처에 포함하지 않음.
- 확장 프로그램 없이 테스트: `python -m app.cli translate 교안.pdf --lang ru --course secure-coding`
- 테스트: `python -m pytest -q`

## API
| 메서드 | 경로 | 입력 | 출력 |
|---|---|---|---|
| POST | `/documents` | `file`(PDF), `lang`, `course`(선택) | `doc_id`, `pages[] {page, original, translated, terms, issues}`, `issue_pages`, `needs_review` |
| GET | `/documents/{doc_id}` | | 저장된 결과 |
| POST | `/glossary/candidates` | `file`, `use_llm` | 검수용 용어 후보 |
| GET | `/notices` | `source=school 또는 dept`, `limit=1~30` (기본 10), `page>=1` | `items`, `page`, `has_more` |
| POST | `/notices/extract` | `{items: [{id, title, body}]}` 최대 30건 | `{items: [{id, summary, deadline, deadline_text, action}]}` |

## 공지 수집 및 한국어 정보 추출 (v3)
- API 계약은 `ext/notice-tab`에서 합의한 공지 v3 형식. `lang` 없이 한국어 원문 `title`, `body`와 `summary`, `deadline`, `deadline_text`, `action`을 반환. `*_tr` 필드는 제거.
- 학교·학과 게시판의 최근 3페이지를 수집. `NOTICE_CRAWL_PAGES`로 범위 조절. `has_more`는 이 수집 범위 기준이며 전체 과거 공지 검색은 아직 미지원.
- 서버 시작 시 백그라운드 수집을 시작하고 주기적으로 갱신. GET 요청은 수집을 기다리지 않고 저장된 목록을 즉시 반환.
- 저장된 목록이 아직 없으면 HTTP 503 + `detail`. 최초 수집 완료 후 다시 요청. 정상적으로 수집한 빈 목록은 HTTP 200.
- 성공 후 30분 간격으로 갱신. 실패 후 5분 간격 재시도. 실패·갱신 중에도 이전에 받은 목록(빈 목록 포함)은 계속 표시.
- `robots.txt` 확인: 404는 허용, 금지 규칙·서버 오류·통신 오류는 수집 중지. User-Agent `LMS-Helper/0.1`, 사이트 요청 간격 1초 이상.
- 날짜는 점·하이픈·슬래시 형식을 지원. 잘못된 날짜가 있는 행은 건너뛰어 다른 공지에 영향을 주지 않음.
- 본문은 새 글만 요청. 요약·마감일·할 일은 공통 추출기로 처리하고 제목+본문의 내용 해시를 키로 저장. 동일 내용은 재시작·다른 ID·다른 API 경로에서도 LLM을 재호출하지 않음. 내용이 바뀌면 다시 추출.
- 기존 원문 캐시는 재사용하고 아직 없는 추출 정보만 생성. 원문은 `data/notices/`, 추출 캐시는 `data/notice-extractions/`에 저장.
- LLM 출력은 구조 검증. `deadline_text`가 원문에 실제로 존재하고 그 안의 연·월·일이 `deadline`과 일치할 때만 유지. 연도 없는 날짜·상대 날짜·추측한 날짜는 보수적으로 null 처리.
- `POST /notices/extract`는 HTML 없는 평문을 받음. 최대 30건, 항목당 title 2,000자·body 100,000자. LLM 호출·출력 오류 시 HTTP 502 + `detail`; 실패한 결과는 캐시하지 않음.
- LLM은 `.env`의 설정 사용. 서버 시작 시 새 공지가 있으면 추출을 위해 유료 LLM API가 호출될 수 있음. 테스트는 가짜 LLM과 가짜 HTTP만 사용.
- 중복 수집 방지를 위해 서버 worker는 **1개**로 실행. 여러 프로세스·인스턴스 운영의 공유 잠금은 미지원.
- 확인: `/notices?source=school&limit=10&page=1` 및 `source=dept`.

## 주의
- 로컬 또는 접근이 제한된 환경에서만 실행 (번역된 교안이 외부에 공개되지 않게)
- `.env`와 `data/`는 커밋하지 않음
- 교안 서명 다운로드 URL은 서버로 보내지 말고 파일 내용만 전송
