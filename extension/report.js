// 학습 리포트: 정답률, 약한 페이지, 시험 예상 (확정 아님)
const ReportView = (() => {
  const { h } = App;
  const pct = x => `${Math.round((x || 0) * 100)}%`;

  async function render() {
    const { t, params } = App;
    App.status(t.loading);
    let r;
    try {
      const q = { anon_id: App.anonId, lang: App.lang };
      if (params.get('doc_id')) q.doc_id = params.get('doc_id');
      r = await Api.report(q);
    } catch (e) {
      App.status(`${t.errServer} (${e.message})`, { error: true, retry: render });
      return;
    }
    const all = await chrome.storage.local.get(null).catch(() => ({}));
    const docName = id => all[`docmeta:${id}`]?.name || id?.slice(0, 8) || '';
    const docLink = (id, page) => {
      const m = all[`docmeta:${id}`];
      return m ? `viewer.html?${new URLSearchParams({ file: m.file, course: m.course, name: m.name, courseName: m.courseName || '', page })}` : null;
    };

    const app = document.getElementById('app');
    if (!r.quizzes_taken && !r.questions_asked) {
      app.replaceChildren(h('div', { class: 'status' }, h('p', {}, t.noData)));
      return;
    }
    const tile = (label, value) => h('div', { class: 'tile' }, h('span', { class: 'muted' }, label), h('strong', {}, value));
    const typeRows = Object.entries(r.accuracy_by_type || {}).map(([k, v]) =>
      h('div', { class: 'type-row' }, h('span', {}, t.types[k] || k), h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill', style: `width:${pct(v)}` })), h('span', { class: 'num' }, pct(v))));
    const weak = (r.weak_pages || []).map(w => {
      const link = docLink(w.doc_id, w.page);
      const label = `${docName(w.doc_id)} · ${t.page} ${w.page}`;
      return h('li', {}, link ? h('a', { href: link, target: '_blank' }, label) : label, h('span', { class: 'muted' }, ` ${t.wrongCount(w.wrong)}`));
    });
    const points = (r.exam_points || []).map(p => h('div', { class: 'point' },
      h('strong', {}, p.point_tr), h('p', {}, p.why_tr),
      h('div', { class: 'chips' }, (p.pages || []).map(n => h('span', { class: 'chip' }, `${t.page} ${n}`)))));

    app.replaceChildren(h('div', { class: 'report' },
      h('div', { class: 'tiles' }, tile(t.quizzesTaken, r.quizzes_taken ?? 0), tile(t.accuracy, pct(r.accuracy)), tile(t.asked, r.questions_asked ?? 0)),
      typeRows.length ? h('section', {}, h('h2', {}, t.byType), typeRows) : null,
      weak.length ? h('section', {}, h('h2', {}, t.weakPages), h('ul', { class: 'weak' }, weak)) : null,
      points.length ? h('section', {}, h('h2', {}, t.examPoints, h('span', { class: 'tag' }, t.examNote)), points) : null,
      r.summary_tr ? h('section', {}, h('h2', {}, t.summary), h('p', {}, r.summary_tr)) : null));
  }

  return { render };
})();
