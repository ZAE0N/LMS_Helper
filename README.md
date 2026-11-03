# LMS Helper

외국인 유학생이 학교 LMS 안에서 교안과 공지를 모국어로 이해하고 복습할 수 있도록 돕는 크롬 확장 프로그램입니다.
2026 AID 아이디어 공모전 출품작

## ✨ 주요 기능
- **교안 번역 보기**: LMS의 PDF 교안을 원본 슬라이드 모양 그대로 번역해 같은 자리에 보여 줍니다. 전공 용어는 용어집으로 일관되게 번역하고, 코드는 그대로 두며, 그림 속 글자도 인식해 번역합니다. 번역본은 PDF로 저장할 수 있습니다.
- **교안 챗봇**: 보고 있는 교안에 대해 모국어로 질문하면 교안 내용에 근거해 답하고, 근거 쪽을 함께 보여 줍니다.
- **복습**: 객관식·주관식·연결형 문제를 만들고 채점합니다. 학습 리포트로 정답률, 자주 틀린 부분, 시험 예상 포인트를 확인합니다.
- **공지 모아 보기**: 수업·학교·학과 공지를 한곳에 모으고, 공지마다 요약·마감일·할 일을 함께 보여 줍니다.

지원 언어: English, Русский (한국어를 고르면 교안은 원문 그대로)

## 🧩 구조
```
LMS 페이지 ── extension/  크롬 확장 (MV3, 순수 JS)
                 │  오른쪽 서랍(공지·교안·복습), 교안 뷰어, 챗봇
                 ⇅ HTTP (JSON)
              server/     FastAPI 서버
                    번역(용어집 파이프라인), 그림 속 글자 OCR, 공지 수집·요약, 챗봇, 문제, 리포트
```

## 🛠 기술 스택
- 확장 프로그램: Chrome Extension (Manifest V3), JavaScript
- 서버: Python, FastAPI, PyMuPDF, RapidOCR
- AI: OpenAI GPT-4o mini

## 🚀 실행 방법
**서버**
```bash
cd server
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # LLM_API_KEY, LLM_MODEL=gpt-4o-mini 입력
uvicorn app.main:app --port 8000
```

**확장 프로그램**
1. `chrome://extensions` → 개발자 모드 켜기
2. "압축해제된 확장 프로그램을 로드합니다" → `extension/` 폴더 선택
3. LMS 페이지를 새로고침하면 오른쪽에 LMS Helper 서랍이 나타납니다

## 🔒 개인정보·보안
- LMS 비밀번호나 접근 토큰을 저장하지 않고, 로그인된 화면을 그대로 이용합니다.
- 퀴즈·시험 화면에서는 기능이 자동으로 꺼집니다.
- AI API 키는 서버에만 두며, 교안 파일과 번역 결과는 저장소에 올리지 않습니다.

## 👥 팀
| 이름 | 역할 | 담당 |
|---|---|---|
| 전정현 | 팀장 | PM, 확장 프로그램 개발 |
| 카로프 파루흐 | 팀원 | 아이디어 제안, 기획, 서버 & 백엔드, 용어집 번역·번역 품질 검수 |

## 📄 문서
- [`AGENTS.md`](AGENTS.md): 작업 규칙과 API 형식
- [`server/README.md`](server/README.md): 서버 처리 순서와 용어집 규칙
- [`extension/README.md`](extension/README.md): 확장 프로그램 구성

글꼴: [Pretendard](https://github.com/orioncactus/pretendard) (SIL Open Font License, `extension/fonts/`)
