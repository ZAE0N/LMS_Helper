# LMS Helper — 크롬 확장 프로그램

역할 분담: **LMS 화면과 공지 = 크롬 번역**, **교안 = AI 번역(용어집)**

- LMS 모든 페이지 오른쪽 끝 **"LMS Helper" 손잡이 → 서랍**(알림 · 교안 · 복습 탭)을 **한국어로** 넣고, 번역은 크롬 번역이 함. 퀴즈·시험 페이지 제외, Esc로 닫기
- 데이터는 서랍을 처음 열 때 불러옴 (페이지마다 API 호출하지 않음). 교안은 과목별로 펼칠 때 그 과목만 불러옴
- 교안 뷰어(+챗봇)·복습·리포트는 확장 프로그램 화면(`viewer.html`)에서 열림. 문구는 `i18n.js`(영어·러시아어)
- 사이드 패널: 교안 언어 설정, 테마, 개발용 진단

| 파일 | 역할 |
|---|---|
| `config.js` | 서버 주소, `MOCK_API`(기능별 가짜 응답 스위치), 과목명→용어집, 언어·개수 목록, 상단 안내 켜기 |
| `content.js` | LMS 페이지: Canvas API 읽기(공지 HTML→평문), 크롬 번역 감지, 상단 안내(토스트), 테마, 진단(번역 차단 확인 포함) |
| `widgets.js` / `widgets.css` | 오른쪽 서랍 (알림·교안(과목별)·복습). 같은 공지는 하나로 합침 |
| `mock.js` | `MOCK_API.notices`일 때 학교·학과 공지 예시 + 마감일·할 일 추출 흉내 |
| `fake.js` | `MOCK_API.chat/quiz/report`일 때 챗봇·문제·리포트 가짜 응답 (교안 페이지로 만든 "[demo]" 예시) |
| `background.js` | LMS 페이지 대신 서버 요청(CORS), 확장 화면 열기 |
| `viewer.html` / `viewer.css` | 확장 화면 틀 (`?mode=` 없음: 교안+챗봇, `review`: 복습, `report`: 리포트) |
| `viewer.js` | 공통: 교안 받기(뷰어가 직접 → 안 되면 LMS 탭에서 1MB씩) → `/documents` 번역, 같은 파일은 저장된 번역 재사용 |
| `doc.js` / `quiz.js` / `report.js` / `main.js` | 교안 뷰어+챗봇(+PDF 보기 베타: 서버가 교안마다 한 번 만든 모양 유지 PDF) / 복습 문제 / 리포트 / 화면 선택 |
| `sidepanel.*` | 사이드 패널 |
| `api.js` | 확장 화면의 서버 요청 |
| `i18n.js` | 확장 화면 문구 (영어·러시아어) |
| `theme.css` | LMS 테마 |

- 수업 공지: 열린 LMS에서 최근 180일 공지를 읽고, 마감일·할 일은 서버(`/notices/extract`)가 한국어로 추출
- 크롬 번역 연동: 번역 전이면 상단에 "크롬 번역을 켜세요(항상 한국어 번역)" 안내(클릭 불가, 퀴즈 페이지 제외, `CT_TOAST`로 끄기). 번역되면 교안 언어도 그 언어로 자동 설정(지원하지 않으면 영어)
- 서버에 기능이 생기면 `config.js`의 `MOCK_API`에서 그 기능만 `false`로
- 저장(`chrome.storage.local`): `doc:{파일}:{언어}`(doc_id), `docmeta:{doc_id}`, `chat:{doc_id}`(summary·history·log), `lastQuiz`
