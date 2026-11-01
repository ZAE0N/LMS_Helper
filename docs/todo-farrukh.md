# 파루흐 할 일 (구현 마감 11/2 22:00)

담당: `server/`만. 브랜치: `server/pdf-password`
(정현의 `ext/structured-view`가 main에 합쳐졌으면 main에서, 아직이면 `ext/structured-view`에서 시작)

## 1. 비밀번호 걸린 PDF 열기 (`POST /documents`) — 가장 먼저 (실제 교안 중 비밀번호 PDF가 있어 복습·교안 보기가 막힘)
지금은 비밀번호 PDF를 올리면 `400 document closed or encrypted`.

만들 것
1. `POST /documents`에 선택 항목 `password`(문자열) 추가
2. PyMuPDF로 열었을 때 `doc.needs_pass`이면
   - 비밀번호 없음 → `401 {"detail": "PDF_PASSWORD_REQUIRED"}`
   - `doc.authenticate(password)`가 0 → `401 {"detail": "PDF_PASSWORD_WRONG"}`
   - 맞으면 **메모리에서 잠금을 푼 사본**(`doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)`)으로
     이후 번역·페이지 그림·`save_original` 모두 처리
3. 비밀번호는 로그·파일·오류 문구에 절대 남기지 않음
4. 열 때 비밀번호가 필요 없는 PDF(복사·인쇄만 막힌 소유자 비밀번호)는 지금처럼 동작
5. 테스트 `tests/test_documents_password.py` (가짜 LLM)
   - 잠긴 PDF 만들기: `doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="x")`
   - 없음 → 401 REQUIRED / 틀림 → 401 WRONG / 맞음 → 200 + 페이지 / 로그(`caplog`)에 비밀번호 없음
6. `AGENTS.md` 3장 교안에 `password`와 401 두 가지 추가 → 합치기 전에 정현에게 알림
7. 끝나면 정현에게 알려서 확장 비밀번호 입력 화면과 같이 확인

## 2. 정현 PR(`ext/structured-view`) 확인
서버 파일도 바뀜 (`structure.py`, `service.py`, `figure_ocr.py`, `translate.py`, `protect.py`, `main.py`)
- 요약
  - 교안 응답: 페이지 그림(`image`, `background`) + 글 덩어리 위치(`blocks[].rect/box`) → 확장이 원본 모양 위에 번역
  - 블록 종류: `label`(그림 속 글자), `comment`·`codetext`·`codeline`(코드 속 한글), `deco`(머리·꼬리 장식)
  - 통째 그림 속 한글은 OCR(`rapidocr`, 처음 한 번 한국어 모델 약 13MB 내려받음)
  - `translate.py` 고유명사 규칙 + `PROMPT_VERSION`, `protect.py` 괄호 속 한글은 코드로 보지 않음
- 확인: `pip install -r requirements.txt` → `python -m pytest -q` → 확장에서 교안 하나 열어 보기

## 3. 복습 문제 개선 (정현과 범위 정한 뒤)
- 정현이 고른 항목 중 서버(`quiz.py`) 쪽 작업. 범위는 11/1 밤에 정해서 알려 줌

## 참고
- 22:00: 각자 main에 합치고, 둘 다 main으로 확장+서버를 함께 실행
- 커밋은 작게: `feat(server): ...`, `test(server): ...`
