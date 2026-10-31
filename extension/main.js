// 화면 선택: mode=doc(기본, 교안+챗봇) | review(복습) | report(리포트)
(async () => {
  const { params } = App;
  const saved = await App.store.get(['uiLang', 'anonId']);
  App.lang = pickLang(saved.uiLang);
  App.t = I18N[App.lang];
  App.anonId = saved.anonId || null;
  const { t, lang } = App;
  document.documentElement.lang = lang;
  document.getElementById('lang').textContent = I18N[lang].langName;

  const mode = ['review', 'report'].includes(params.get('mode')) ? params.get('mode') : 'doc';
  const demo = { doc: CONFIG.MOCK_API.chat, review: CONFIG.MOCK_API.quiz, report: CONFIG.MOCK_API.report }[mode];
  const demoTag = document.getElementById('demo');
  demoTag.hidden = !demo;
  demoTag.textContent = t.demo;

  const title = { doc: t.viewerTitle, review: t.reviewTitle, report: t.reportTitle }[mode];
  const name = params.get('name') || '';
  document.getElementById('title').textContent = mode === 'report' ? title : `${title} · ${name}`;
  document.getElementById('sub').textContent = params.get('courseName') || '';
  document.title = `${title}${name && mode !== 'report' ? ` · ${name}` : ''} — LMS Helper`;

  if (mode === 'report') return ReportView.render();

  async function open() {
    try {
      const doc = await App.loadDoc(text => App.status(text));
      if (mode === 'review') await QuizView.render(doc);
      else DocView.render(doc);
    } catch (e) {
      App.status(e instanceof App.UserError ? e.message : e instanceof TypeError ? t.errServer : `${t.errServerFail} (${e.message})`, { error: true, retry: open });
    }
  }
  open();
})();
