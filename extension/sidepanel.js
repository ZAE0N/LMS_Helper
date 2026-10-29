const LMS = CONFIG.LMS_ORIGIN + '/';
const out = document.getElementById('out');
const show = v => (out.textContent = typeof v === 'string' ? v : JSON.stringify(v, null, 2));

async function lmsTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url?.startsWith(LMS)) throw new Error('LMS 탭을 연 상태에서 실행하세요');
  return tab;
}

document.querySelectorAll('[data-check]').forEach(btn =>
  btn.addEventListener('click', async () => {
    show('실행 중...');
    try {
      const tab = await lmsTab();
      show(await chrome.tabs.sendMessage(tab.id, { type: btn.dataset.check }));
    } catch (e) {
      const msg = /Receiving end does not exist/.test(e.message) ? 'LMS 탭을 새로고침한 뒤 다시 시도하세요' : e.message;
      show({ ok: false, error: msg });
    }
  })
);

const theme = document.getElementById('theme');
chrome.storage.local.get('theme').then(({ theme: t }) => (theme.checked = t === 'clean'));
theme.addEventListener('change', () => chrome.storage.local.set({ theme: theme.checked ? 'clean' : 'off' }));
