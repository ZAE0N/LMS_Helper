// 확장 프로그램 화면(교안 뷰어 등)의 서버 요청은 이 파일에서만 한다
const Api = (() => {
  async function request(path, options = {}) {
    const r = await fetch(CONFIG.SERVER_URL + path, {
      ...options,
      headers: { ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.detail || `HTTP ${r.status}`), { status: r.status, detail: data.detail });
    return data;
  }
  const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) });
  const mock = CONFIG.MOCK_API;

  return {
    request,
    // password: 비밀번호 걸린 PDF일 때만 (저장하지 않고 이 요청에만 씀)
    translateDocument(blob, name, lang, course, password) {
      const fd = new FormData();
      fd.append('file', blob, name || 'document.pdf');
      fd.append('lang', lang);
      if (course) fd.append('course', course);
      if (password) fd.append('password', password);
      return request('/documents', { method: 'POST', body: fd });
    },
    getDocument: id => request(`/documents/${id}`),
    // doc은 가짜 응답을 만들 때만 사용 (서버로는 보내지 않음)
    chat: (body, doc) => (mock.chat ? FAKE.chat(body, doc) : post('/chat', body)),
    quiz: (body, doc) => (mock.quiz ? FAKE.quiz(body, doc) : post('/quiz', body)),
    gradeShort: body => (mock.quiz ? FAKE.gradeShort(body) : post('/quiz/grade-short', body)),
    quizResult: body => (mock.quiz ? FAKE.quizResult(body) : post('/quiz/result', body)),
    report: q => (mock.report ? FAKE.report(q) : request(`/report?${new URLSearchParams(q)}`))
  };
})();
