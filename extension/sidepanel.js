const $ = s => document.querySelector(s);
const state = { lang: 'en' };
const t = () => I18N[state.lang];

const store = {
  async get(keys) {
    try { return await chrome.storage.local.get(keys); } catch { return {}; }
  },
  set(obj) {
    return chrome.storage.local.set(obj).catch(() => {});
  }
};

function applyI18n() {
  document.documentElement.lang = state.lang;
  document.querySelectorAll('[data-i18n]').forEach(el => (el.textContent = t()[el.dataset.i18n]));
}

async function lmsTab() {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active?.url?.startsWith(CONFIG.LMS_ORIGIN)) return active;
  const [any] = await chrome.tabs.query({ url: `${CONFIG.LMS_ORIGIN}/*` });
  return any || null;
}

async function askLms(msg) {
  const tab = await lmsTab();
  if (!tab) return null;
  const res = await chrome.tabs.sendMessage(tab.id, msg);
  if (!res?.ok) throw new Error(res?.error || 'LMS error');
  return res.data;
}

// 교안 뷰어 언어. 크롬 번역 언어를 따라감 (지원하지 않으면 영어)
function setLang(code) {
  if (code === state.lang) return;
  state.lang = code;
  $('#lang').value = code;
  store.set({ uiLang: code });
  applyI18n();
}

function syncChrome({ translated, lang }) {
  $('#ct-warn').hidden = true;
  if (!translated || !lang) {
    $('#synced').hidden = true;
    return;
  }
  const supported = CONFIG.LANGS.includes(lang);
  setLang(supported ? lang : 'en');
  $('#ct-warn').hidden = supported;
  $('#synced').hidden = false;
}

async function init() {
  const saved = await store.get(['uiLang', 'theme']);
  state.lang = pickLang(saved.uiLang);
  $('#lang').replaceChildren(...CONFIG.LANGS.map(code => new Option(I18N[code].langName, code, false, code === state.lang)));
  $('#theme').checked = saved.theme === 'clean';
  applyI18n();

  $('#lang').addEventListener('change', e => {
    $('#synced').hidden = true;
    setLang(e.target.value);
  });
  $('#theme').addEventListener('change', e => store.set({ theme: e.target.checked ? 'clean' : 'off' }));

  chrome.runtime.onMessage.addListener((msg, sender) => {
    if (msg.type === 'page-translate' && sender.tab?.url?.startsWith(CONFIG.LMS_ORIGIN)) syncChrome(msg);
  });
  askLms({ type: 'get-translate-state' }).then(s => s && syncChrome(s)).catch(() => {});

  const out = $('#out');
  document.querySelectorAll('[data-check]').forEach(btn =>
    btn.addEventListener('click', async () => {
      out.textContent = '...';
      try {
        const data = await askLms({ type: btn.dataset.check });
        out.textContent = data ? JSON.stringify(data, null, 2) : 'LMS 탭을 열고 새로고침하세요';
      } catch (e) {
        out.textContent = /Receiving end/.test(e.message) ? 'LMS 탭을 새로고침하세요' : e.message;
      }
    })
  );
}

init();
