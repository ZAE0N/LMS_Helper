// 확장 프로그램 화면(교안 뷰어 등)의 서버 요청은 이 파일에서만 한다
const Api = (() => {
  async function request(path, options = {}) {
    const r = await fetch(CONFIG.SERVER_URL + path, {
      ...options,
      headers: { ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  }
  return { request };
})();
