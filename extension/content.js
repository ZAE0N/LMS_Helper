const QUIZ_PATH = /\/(quizzes|quiz_submissions|assessments|assignments\/\d+\/lti)|\/take\b/;

function applyTheme(theme) {
  const on = theme === 'clean' && !QUIZ_PATH.test(location.pathname);
  document.documentElement.classList.toggle('lmsh-clean', on);
}

chrome.storage.local.get('theme').then(({ theme }) => applyTheme(theme));
chrome.storage.onChanged.addListener((c, area) => {
  if (area === 'local' && c.theme) applyTheme(c.theme.newValue);
});
addEventListener('popstate', () => chrome.storage.local.get('theme').then(({ theme }) => applyTheme(theme)));

// 크롬 번역 감지: 켜지면 <html>에 translated-ltr/rtl 클래스가 붙고 lang이 대상 언어로 바뀜 (공식 API 아님)
function translateState() {
  const html = document.documentElement;
  const on = html.classList.contains('translated-ltr') || html.classList.contains('translated-rtl');
  return { translated: on, lang: on ? (html.lang || '').split('-')[0].toLowerCase() || null : null };
}
let lastTranslate = '';
function reportTranslate() {
  const s = translateState();
  updateToast(s);
  const key = JSON.stringify(s);
  if (key === lastTranslate) return;
  lastTranslate = key;
  chrome.runtime.sendMessage({ type: 'page-translate', ...s }).catch(() => {}); // 패널이 닫혀 있으면 무시
}
new MutationObserver(reportTranslate).observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'lang'] });

// 상단 안내 (토스트): 번역 전에는 계속 표시, 클릭 불가, LMS 스타일과 분리(shadow DOM)
const TOAST_TEXT = {
  en: 'Turn on Chrome translation: translate icon in the address bar → your language → "Always translate Korean"',
  ru: 'Включите перевод Chrome: значок перевода в адресной строке → ваш язык → «Всегда переводить с корейского»'
};
let toastHost = null;
function updateToast({ translated }) {
  const show = CONFIG.CT_TOAST && !translated && !QUIZ_PATH.test(location.pathname);
  if (!show) {
    toastHost?.remove();
    toastHost = null;
    return;
  }
  if (toastHost || !document.body) return;
  toastHost = document.createElement('div');
  toastHost.setAttribute('translate', 'no');
  const root = toastHost.attachShadow({ mode: 'open' });
  const lang = (navigator.language || 'en').startsWith('ru') ? 'ru' : 'en';
  const style = document.createElement('style');
  style.textContent = `
    .toast { position: fixed; top: 14px; left: 50%; transform: translateX(-50%); z-index: 2147483647;
      pointer-events: none; user-select: none; max-width: min(640px, 92vw);
      background: rgba(17, 24, 39, .92); color: #fff; border-radius: 999px; padding: 10px 18px;
      font: 14px/1.4 "Segoe UI", "Malgun Gothic", system-ui, sans-serif; box-shadow: 0 6px 20px rgba(0,0,0,.25);
      animation: in .25s ease-out; }
    @keyframes in { from { opacity: 0; transform: translate(-50%, -8px); } }
    :host-context(html.lmsh-drawer-open) .toast { left: calc((100vw - min(640px, 92vw)) / 2); } /* 서랍과 겹치지 않게 */`;
  const box = document.createElement('div');
  box.className = 'toast';
  box.textContent = `🌐 ${TOAST_TEXT[lang]}`;
  root.append(style, box);
  document.body.append(toastHost);
}
document.addEventListener('DOMContentLoaded', () => updateToast(translateState()));
if (document.readyState !== 'loading') updateToast(translateState());

// 세션 인증 JSON 응답 앞의 while(1); 제거
async function canvasGet(path) {
  const r = await fetch(path, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  const t = await r.text();
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return JSON.parse(t.replace(/^while\(1\);/, ''));
}

async function checkSession() {
  const me = await canvasGet('/api/v1/users/self');
  const courses = await canvasGet('/api/v1/courses?enrollment_state=active&per_page=50');
  return {
    result: '세션 API 접근 성공',
    name: me.name,
    courseCount: courses.length,
    courses: courses.map(c => c.name)
  };
}

async function download(url) {
  try {
    const r = await fetch(url, { credentials: 'same-origin' });
    const b = await r.blob();
    const head = new TextDecoder().decode(await b.slice(0, 5).arrayBuffer());
    return { via: 'content', ok: r.ok, status: r.status, finalHost: new URL(r.url).host, bytes: b.size, isPdf: head === '%PDF-' };
  } catch (e) {
    const bg = await chrome.runtime.sendMessage({ type: 'bg-fetch-file', url });
    return { via: 'background', contentError: String(e), ...bg };
  }
}

const smallestPdf = files =>
  files
    .filter(f => /\.pdf$/i.test(f.display_name || f.filename || '') && f.size && f.size <= 10 * 1024 * 1024)
    .sort((a, b) => a.size - b.size)[0];

// 파일 탭 → 실패 시 모듈 경유. 첫 성공에서 중단
async function checkFile() {
  const courses = await canvasGet('/api/v1/courses?enrollment_state=active&per_page=50');
  const tried = [];
  for (const c of courses) {
    let file, route;
    try {
      file = smallestPdf(await canvasGet(`/api/v1/courses/${c.id}/files?per_page=50`));
      route = 'files';
    } catch (e) {
      tried.push({ course: c.name, files: e.message });
    }
    if (!file) {
      try {
        const mods = await canvasGet(`/api/v1/courses/${c.id}/modules?include[]=items&per_page=50`);
        const item = mods.flatMap(m => m.items || []).find(i => i.type === 'File' && i.url);
        if (item) {
          file = await canvasGet(item.url);
          route = 'modules';
        }
      } catch (e) {
        tried.push({ course: c.name, modules: e.message });
      }
    }
    if (!file) {
      tried.push({ course: c.name, note: 'PDF 없음' });
      continue;
    }
    const res = await download(file.url);
    return { course: c.name, route, file: file.display_name, apiSize: file.size, ...res, tried };
  }
  return { ok: false, note: '접근 가능한 PDF를 찾지 못함', tried };
}

// HTML → 평문 (서버에는 평문만 보냄)
function toPlain(html) {
  const marked = (html || '').replace(/<br\s*\/?>/gi, '\n').replace(/<\/(p|div|li|h\d|tr)>/gi, '\n');
  const text = new DOMParser().parseFromString(marked, 'text/html').body.textContent || '';
  return text.replace(/ /g, ' ').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

const ANNOUNCE_DAYS = 180; // 기본값은 최근 14일이라 기간을 직접 지정

// "시큐어코딩-3B-정규-21403" → "시큐어코딩"
const shortCourse = name => (name || '').replace(/-\d[A-Z]-.*$/, '').trim() || name;

async function getAnnouncements({ limit = 10, page = 1 }) {
  const courses = await canvasGet('/api/v1/courses?enrollment_state=active&per_page=50');
  if (!courses.length) return { items: [], page, has_more: false };
  const names = Object.fromEntries(courses.map(c => [`course_${c.id}`, shortCourse(c.name)]));
  const q = new URLSearchParams({
    start_date: new Date(Date.now() - ANNOUNCE_DAYS * 86400000).toISOString(),
    end_date: new Date().toISOString(),
    per_page: String(limit),
    page: String(page)
  });
  Object.keys(names).forEach(code => q.append('context_codes[]', code));
  const list = await canvasGet(`/api/v1/announcements?${q}`);
  const items = list.map(a => ({
    id: `canvas-${a.id}`,
    source: 'course',
    course: names[a.context_code] || null,
    title: a.title,
    body: toPlain(a.message),
    posted_at: a.posted_at || a.delayed_post_at || a.created_at || null,
    updated_at: a.updated_at || a.posted_at || a.created_at || '',
    url: a.html_url
  }));
  return { items, page, has_more: list.length === limit };
}

// 진단: 번역에서 빠진 한국어가 어디 있는지, 번역 차단 표시가 붙어 있는지
function describe(el) {
  const parts = [];
  for (let e = el; e && e !== document.body && parts.length < 4; e = e.parentElement) {
    const cls = typeof e.className === 'string' ? e.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    parts.unshift(`${e.tagName.toLowerCase()}${e.id ? `#${e.id}` : ''}${cls ? `.${cls}` : ''}`);
  }
  return parts.join(' > ');
}
async function checkTranslateBlock() {
  const BLOCK = '[translate="no"], .notranslate';
  const blocked = new Map();
  const leftover = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const text = walker.currentNode.nodeValue.trim();
    const el = walker.currentNode.parentElement;
    if (!/[가-힣]/.test(text) || !el || el.closest('.lmsh-ui, script, style, noscript')) continue;
    if (!el.getClientRects().length) continue; // 화면에 안 보이는 글자 제외
    const b = el.closest(BLOCK);
    if (b) {
      const key = describe(b);
      if (!blocked.has(key)) blocked.set(key, { where: key, translate: b.getAttribute('translate'), notranslate: b.classList.contains('notranslate'), sample: text.slice(0, 30) });
    } else if (leftover.length < 12) leftover.push({ where: describe(el), sample: text.slice(0, 30) });
  }
  return {
    translated: translateState().translated,
    blocked: [...blocked.values()].slice(0, 15),
    leftover_hint: '번역이 켜진 상태에서 아래 목록은 차단 표시 없이 한국어로 남은 글자',
    leftover
  };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const fn = {
    'check-translate-block': checkTranslateBlock,
    'check-session': checkSession,
    'check-file': checkFile,
    'get-announcements': getAnnouncements,
    'get-translate-state': async () => translateState()
  }[msg.type];
  if (!fn) return;
  fn(msg).then(data => sendResponse({ ok: true, data })).catch(e => sendResponse({ ok: false, error: e.message }));
  return true;
});
