chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

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
