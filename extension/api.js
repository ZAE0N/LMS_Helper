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
  const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) });
  const mock = CONFIG.MOCK_API;

  return {
    request,
    translateDocument(blob, name, lang, course) {
      const fd = new FormData();
      fd.append('file', blob, name || 'document.pdf');
      fd.append('lang', lang);
      if (course) fd.append('course', course);
      return request('/documents', { method: 'POST', body: fd });
    },
    getDocument: id => request(`/documents/${id}`),
    // 모양 유지 번역 PDF (베타). 서버가 만드는 중이면 { pending: true }
    async layoutPdf(id) {
      const r = await fetch(`${CONFIG.SERVER_URL}/documents/${id}/pdf`);
      if (r.status === 202) return { pending: true };
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw Object.assign(new Error(data.detail || `HTTP ${r.status}`), { status: r.status });
      }
      return { blob: await r.blob() };
    },
    // doc은 가짜 응답을 만들 때만 사용 (서버로는 보내지 않음)
    chat: (body, doc) => (mock.chat ? FAKE.chat(body, doc) : post('/chat', body)),
    quiz: (body, doc) => (mock.quiz ? FAKE.quiz(body, doc) : post('/quiz', body)),
    gradeShort: body => (mock.quiz ? FAKE.gradeShort(body) : post('/quiz/grade-short', body)),
    quizResult: body => (mock.quiz ? FAKE.quizResult(body) : post('/quiz/result', body)),
    report: q => (mock.report ? FAKE.report(q) : request(`/report?${new URLSearchParams(q)}`))
  };
})();
