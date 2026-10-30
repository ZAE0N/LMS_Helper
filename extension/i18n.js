// 확장 프로그램 화면(사이드 패널·교안 뷰어) 문구. LMS 안 위젯은 한국어 + 크롬 번역
const I18N = {
  en: {
    langName: 'English',
    guide: 'Notices, course materials and review are on the right side of the LMS dashboard.',
    language: 'Language for course materials',
    settings: 'Settings',
    theme: 'Clean LMS theme',
    themeHint: 'Turned off automatically on quiz and exam pages',
    dev: 'Developer tools',
    ctUnsupported: "This language isn't supported yet. Showing English.",
    synced: 'Synced with Chrome',
    viewerTitle: 'Course material',
    reviewTitle: 'Review',
    preparing: 'Translated pages and the chat will appear here (in progress).',
    reviewPreparing: 'Review questions will appear here (in progress).',
    types: { multiple: 'Multiple choice', short: 'Short answer', matching: 'Matching' },
    questions: 'questions'
  },
  ru: {
    langName: 'Русский',
    guide: 'Объявления, материалы курса и повторение — справа на панели управления LMS.',
    language: 'Язык материалов',
    settings: 'Настройки',
    theme: 'Аккуратная тема LMS',
    themeHint: 'Автоматически отключается на страницах тестов и экзаменов',
    dev: 'Для разработчика',
    ctUnsupported: 'Этот язык пока не поддерживается. Показываем английский.',
    synced: 'Как в Chrome',
    viewerTitle: 'Материал курса',
    reviewTitle: 'Повторение',
    preparing: 'Здесь появятся переведённые страницы и чат (в разработке).',
    reviewPreparing: 'Здесь появятся вопросы для повторения (в разработке).',
    types: { multiple: 'С вариантами', short: 'Короткий ответ', matching: 'Сопоставление' },
    questions: 'вопросов'
  }
};

function pickLang(saved) {
  if (CONFIG.LANGS.includes(saved)) return saved;
  const nav = (navigator.language || 'en').slice(0, 2);
  return CONFIG.LANGS.includes(nav) ? nav : 'en';
}
