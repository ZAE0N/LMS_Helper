// 서버 주소는 여기서만 바꾼다 (manifest host_permissions도 함께)
const CONFIG = {
  SERVER_URL: 'http://localhost:8000',
  LMS_ORIGIN: 'https://lms-app.inhatc.ac.kr',
  // 서버에 아직 없는 기능은 가짜 응답(true). 서버가 준비되면 false로
  MOCK_API: { notices: false, chat: true, quiz: true, report: true },
  // LMS 과목명(짧은 이름) → 서버 용어집 파일명 (server/glossary/<이름>.json)
  GLOSSARY: { '시큐어코딩': 'secure-coding' },
  LANGS: ['en', 'ru'],
  LIMITS: [10, 20, 30],
  CT_TOAST: true // LMS가 번역 전이면 상단에 "크롬 번역을 켜세요" 안내
};
