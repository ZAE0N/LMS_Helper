importScripts('config.js');

// 확장 아이콘: LMS 탭이면 서랍 열고 닫기, 아니면 LMS 열기
chrome.action.onClicked.addListener(tab => {
  if (tab.url?.startsWith(CONFIG.LMS_ORIGIN)) chrome.tabs.sendMessage(tab.id, { type: 'toggle-drawer' }).catch(() => {});
  else chrome.tabs.create({ url: CONFIG.LMS_ORIGIN });
});

// LMS 페이지(콘텐츠 스크립트)는 CORS 때문에 서버로 직접 못 보냄 → 여기서 대신 요청
async function callServer({ method = 'GET', path, body }) {
  const r = await fetch(CONFIG.SERVER_URL + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'api') {
    callServer(msg).then(data => sendResponse({ ok: true, data })).catch(e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg.type === 'open-page') {
    // 연 LMS 탭 번호를 넘겨서, 뷰어가 직접 못 받는 파일은 그 탭에서 받아 오게 함
    const sep = msg.page.includes('?') ? '&' : '?';
    chrome.tabs.create({ url: chrome.runtime.getURL(`${msg.page}${sender.tab ? `${sep}tab=${sender.tab.id}` : ''}`) });
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  const { anonId } = await chrome.storage.local.get('anonId');
  if (!anonId) await chrome.storage.local.set({ anonId: crypto.randomUUID(), theme: 'off' });
});

// 콘텐츠 스크립트에서 CORS로 막힌 파일 다운로드를 재시도
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type !== 'bg-fetch-file') return;
  (async () => {
    try {
      const r = await fetch(msg.url, { credentials: 'include' });
      const b = await r.blob();
      const head = new TextDecoder().decode(await b.slice(0, 5).arrayBuffer());
      sendResponse({ ok: r.ok, status: r.status, finalHost: new URL(r.url).host, bytes: b.size, isPdf: head === '%PDF-' });
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true;
});
