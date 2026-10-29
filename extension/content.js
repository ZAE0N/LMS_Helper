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

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const fn = { 'check-session': checkSession, 'check-file': checkFile }[msg.type];
  if (!fn) return;
  fn().then(data => sendResponse({ ok: true, data })).catch(e => sendResponse({ ok: false, error: e.message }));
  return true;
});
