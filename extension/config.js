// 서버 주소는 여기서만 바꾼다 (manifest host_permissions도 함께)
const CONFIG = {
  SERVER_URL: 'http://localhost:8000',
  LMS_ORIGIN: 'https://lms-app.inhatc.ac.kr',
  USE_MOCK: true, // 서버 준비되면 false
  LANGS: ['en', 'ru'],
  LIMITS: [10, 20, 30],
  CT_TOAST: true // LMS가 번역 전이면 상단에 "크롬 번역을 켜세요" 안내
};
