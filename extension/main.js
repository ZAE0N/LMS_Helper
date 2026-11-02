// 화면 선택: mode=doc(기본, 교안+챗봇) | review(복습) | report(리포트)
(async () => {
  const { params } = App;
  const saved = await App.store.get(['uiLang', 'anonId']);
  App.lang = pickLang(saved.uiLang);
  App.t = I18N[App.lang];
  App.anonId = saved.anonId || null;
  const { t, lang } = App;
  document.documentElement.lang = lang;
  const mode = ['review', 'report'].includes(params.get('mode')) ? params.get('mode') : 'doc';
  const langEl = document.getElementById('lang');
  if (mode === 'doc') { // 교안 화면에서만 언어를 바꿈 → 그 언어로 다시 엶 (처음이면 서버가 번역)
    langEl.replaceWith(App.h('select', { class: 'lang-pick', id: 'lang', 'aria-label': t.docLang, translate: 'no',
      onchange: async e => { await App.store.set({ uiLang: e.target.value, uiLangManual: true }); location.reload(); } },
    CONFIG.LANGS.map(code => App.h('option', { value: code, selected: code === lang }, I18N[code].langName))));
  } else langEl.textContent = I18N[lang].langName;
  const demo = { doc: CONFIG.MOCK_API.chat, review: CONFIG.MOCK_API.quiz, report: CONFIG.MOCK_API.report }[mode];
  const demoTag = document.getElementById('demo');
  demoTag.hidden = !demo;
  demoTag.textContent = t.demo;

  const title = { doc: t.viewerTitle, review: t.reviewTitle, report: t.reportTitle }[mode];
  const name = params.get('name') || '';
  document.getElementById('title').textContent = mode === 'report' ? title : mode === 'doc' ? name || title : `${title} · ${name}`;
  document.getElementById('sub').textContent = params.get('courseName') || '';
  document.title = `${title}${name && mode !== 'report' ? ` · ${name}` : ''} — LMS Helper`;

  if (mode === 'report') return ReportView.render();

  async function open(password = null) {
    try {
      // save=1: 서랍의 "번역본 ⬇" → 번역(처음이면 변환) 후 바로 인쇄 → PDF로 저장
      const save = mode === 'doc' && params.get('save') === '1';
      const doc = await App.loadDoc(text => App.status(save ? `${t.converting} · ${text}` : text), { password });
      if (mode === 'review') await QuizView.render(doc);
      else DocView.render(doc, { autoSave: save });
    } catch (e) {
      if (e instanceof App.PasswordError) {
        return App.askPassword(e.wrong, pw => open(pw), () => App.status(t.passwordCancelled, { error: true, retry: () => open() }));
      }
      App.status(e instanceof App.UserError ? e.message : e instanceof TypeError ? t.errServer : `${t.errServerFail} (${e.message})`, { error: true, retry: () => open() });
    }
  }
  open();
})();
