# 파루흐 할 일 (11/2 22:00 구현 마감)

담당: `server/`만. 브랜치: `server/pdf-password`
(정현의 `ext/structured-view` PR이 main에 합쳐졌으면 main에서, 아직이면 `ext/structured-view`에서 시작)

## 1. 비밀번호 걸린 PDF 열기 (`POST /documents`)
지금은 비밀번호가 걸린 교안을 올리면 `document closed or encrypted` 오류가 남.

만들 것
1. `POST /documents`에 선택 항목 `password`(문자열) 추가
2. PyMuPDF로 열었을 때 `doc.needs_pass`이면
   - 비밀번호 없음 → `401 {"detail": "PDF_PASSWORD_REQUIRED"}`
   - `doc.authenticate(password)`가 0 → `401 {"detail": "PDF_PASSWORD_WRONG"}`
   - 맞으면 **메모리에서 잠금을 푼 사본**을 만들고(`doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)`)
     이후 번역·페이지 그림·`save_original` 모두 이 사본으로 처리
3. 비밀번호는 로그·파일·오류 문구에 절대 남기지 않음
4. 열 때는 비밀번호가 필요 없고 복사·인쇄만 막힌 PDF(소유자 비밀번호)는 지금처럼 그대로 동작해야 함
5. 테스트 `tests/test_documents_password.py` (다른 테스트처럼 가짜 LLM 사용)
   - 테스트 안에서 잠긴 PDF 만들기: `doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="x")`
   - 비밀번호 없음 → 401 REQUIRED / 틀림 → 401 WRONG / 맞음 → 200, 페이지 있음
   - 로그(`caplog`)에 비밀번호가 없는지
6. `AGENTS.md` 3장 교안에 `password` 항목과 401 오류 두 가지 추가 → 합치기 전에 정현에게 알림

## 2. 정현 PR(`ext/structured-view`) 확인
이 PR은 서버 파일도 바꿈 (`structure.py`, `service.py`, `figure_ocr.py`, `translate.py`, `main.py`)
- 바뀐 점 요약
  - 교안 응답: 페이지 그림(`image`, `background`)과 글 덩어리 위치(`blocks[].rect/box`) → 확장이 원본 모양 위에 번역을 올림
  - 블록 종류: `label`(그림 속 글자), `comment`(코드 주석), `codetext`·`codeline`(코드 속 한글), `deco`(머리·꼬리 장식)
  - 통째 그림 속 한글은 OCR(`rapidocr`)로 찾아 번역
  - 번역 규칙(`translate.py` SYSTEM)에 고유명사 규칙 추가, `PROMPT_VERSION`을 페이지 캐시 키에 넣음
- 확인 방법
  - `pip install -r requirements.txt` (새로 추가: `rapidocr`, `onnxruntime`) → `python -m pytest -q`
  - 확장에서 교안 하나를 열어 슬라이드 보기가 나오는지
  - 처음 OCR을 쓸 때 한국어 모델(약 13MB)을 내려받음. 서버 로그에 `그림 OCR 사용 불가`/`그림 OCR 실패`가 나오면 정현에게 알림

## 참고
- 형식·번역 규칙 버전이 바뀌어 교안마다 한 번씩 전체를 새로 번역함 (교안당 토큰 한 번)
- 커밋은 작게: `feat(server): ...`, `test(server): ...`
