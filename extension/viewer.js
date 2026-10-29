// 교안 뷰어 + 챗봇 / 복습 화면 (다음 단계에서 구현). 지금은 전달받은 값만 표시
(async () => {
  const q = new URLSearchParams(location.search);
  const { uiLang } = await chrome.storage.local.get('uiLang').catch(() => ({}));
  const lang = pickLang(uiLang);
  const t = I18N[lang];
  document.documentElement.lang = lang;

  const review = q.get('mode') === 'review';
  document.getElementById('title').textContent = `${review ? t.reviewTitle : t.viewerTitle} · ${q.get('name') || ''}`;
  document.getElementById('sub').textContent = q.get('courseName') || '';
  document.getElementById('msg').textContent = review ? t.reviewPreparing : t.preparing;
  if (review) {
    const chips = (q.get('types') || '').split(',').filter(Boolean).map(k => t.types[k] || k);
    chips.push(`${q.get('count') || 5} ${t.questions}`);
    document.getElementById('chips').replaceChildren(...chips.map(c => Object.assign(document.createElement('span'), { className: 'chip', textContent: c })));
  }
})();
