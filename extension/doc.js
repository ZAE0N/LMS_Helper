// 교안 뷰어: 페이지 목록 · 원문/번역 · 챗봇
const DocView = (() => {
  const { h } = App;
  const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // PDF 글머리표(사용자 정의 문자 U+E000~F8FF)는 글꼴이 없어 □로 보이므로 •로 표시
  const clean = s => (s || '').replace(/[\uE000-\uF8FF]/g, '• ');

  // 원문에서 용어집 용어를 <mark>로 표시
  function highlighted(text, terms) {
    const box = h('div', { class: 'text' });
    if (!terms.length) return (box.textContent = text), box;
    const re = new RegExp(`(${[...terms].sort((a, b) => b.length - a.length).map(escapeRe).join('|')})`, 'g');
    text.split(re).forEach((part, i) => box.append(i % 2 ? h('mark', {}, part) : part));
    return box;
  }

  function render(doc) {
    const { t } = App;
    const app = document.getElementById('app');
    const pages = doc.pages;
    let current = 0;
    let mode = 'side';

    const list = h('nav', { class: 'pages', 'aria-label': t.pages },
      pages.map((p, i) => h('button', { type: 'button', class: 'pg', 'data-i': i, onclick: () => go(i) },
        `${t.page} ${p.page}`, p.issues?.length ? h('span', { class: 'warn-dot', title: t.issuePage }, '!') : null)));
    const counter = h('span', { class: 'counter' });
    const prev = h('button', { type: 'button', class: 'btn ghost', onclick: () => go(current - 1) }, `‹ ${t.prev}`);
    const next = h('button', { type: 'button', class: 'btn ghost', onclick: () => go(current + 1) }, `${t.next} ›`);
    const modes = [['side', t.side], ['tr', t.translated], ['or', t.original], ['pdf', t.pdfMode]].map(([k, label]) =>
      h('button', { type: 'button', 'data-m': k, onclick: () => ((mode = k), paint()) }, label));
    const issue = h('p', { class: 'note warn', hidden: true }, t.issuePage);
    const cols = h('div', { class: 'cols' });
    const termBox = h('div', { class: 'terms' });
    const pdf = { url: null, error: null, loading: false, frame: h('iframe', { class: 'pdf-frame', title: t.pdfMode }) };

    // 서버가 교안마다 한 번만 만들어 둔 PDF를 받음. 만드는 중이면 5초마다 다시 확인
    async function loadPdf() {
      if (pdf.loading || pdf.url) return;
      pdf.loading = true;
      pdf.error = null;
      try {
        for (;;) {
          const res = await Api.layoutPdf(doc.doc_id);
          if (res.blob) {
            pdf.url = URL.createObjectURL(res.blob);
            break;
          }
          await new Promise(r => setTimeout(r, 5000));
        }
      } catch (e) {
        pdf.error = `${t.pdfError} (${e.message})`;
      }
      pdf.loading = false;
      if (mode === 'pdf') paint();
    }

    function paintPdf(p) {
      cols.className = 'cols pdf';
      termBox.replaceChildren();
      issue.hidden = true;
      if (pdf.url) {
        const src = `${pdf.url}#page=${p.page}`;
        if (pdf.frame.getAttribute('src') !== src) pdf.frame.src = src;
        cols.replaceChildren(h('p', { class: 'note' }, t.pdfNote), pdf.frame);
      } else if (pdf.error) {
        cols.replaceChildren(h('div', { class: 'status error' }, h('p', {}, pdf.error),
          h('button', { type: 'button', class: 'btn', onclick: () => { pdf.error = null; paint(); } }, t.retry)));
      } else {
        cols.replaceChildren(h('div', { class: 'status' }, h('span', { class: 'spinner' }), h('p', {}, t.pdfPreparing)));
        loadPdf();
      }
    }

    function paint() {
      const p = pages[current];
      list.querySelectorAll('.pg').forEach(b => b.classList.toggle('on', Number(b.dataset.i) === current));
      list.querySelector('.pg.on')?.scrollIntoView({ block: 'nearest' });
      modes.forEach(b => b.classList.toggle('on', b.dataset.m === mode));
      counter.textContent = `${p.page} / ${pages[pages.length - 1].page}`;
      prev.disabled = current === 0;
      next.disabled = current === pages.length - 1;
      issue.hidden = !p.issues?.length;
      if (mode === 'pdf') return paintPdf(p);
      const empty = !(p.original || '').trim();
      const tr = h('article', { class: 'col' }, h('h3', {}, t.translated),
        empty ? h('p', { class: 'muted' }, t.noText) : Object.assign(h('div', { class: 'text' }), { textContent: clean(p.translated) }));
      const or = h('article', { class: 'col original', lang: 'ko' }, h('h3', {}, t.original),
        empty ? h('p', { class: 'muted' }, t.noText) : highlighted(clean(p.original), p.terms || []));
      cols.className = `cols ${mode}`;
      cols.replaceChildren(...(mode === 'side' ? [tr, or] : mode === 'tr' ? [tr] : [or]));
      termBox.replaceChildren(...(p.terms?.length ? [h('span', { class: 'muted' }, `${t.terms}:`), ...p.terms.map(x => h('span', { class: 'term' }, x))] : []));
      cols.scrollTop = 0;
    }
    function go(i) {
      current = Math.max(0, Math.min(pages.length - 1, i));
      paint();
    }
    const goPage = n => go(pages.findIndex(p => p.page === n));

    const reader = h('section', { class: 'reader' },
      h('div', { class: 'toolbar' }, prev, counter, next, h('div', { class: 'seg' }, modes)),
      doc.needs_review?.length ? h('p', { class: 'note' }, t.needsReview) : null,
      issue, cols, termBox);
    app.replaceChildren(h('div', { class: 'doc' }, list, reader, Chat(doc, goPage)));
    addEventListener('keydown', e => {
      if (e.target.closest('textarea, input, select')) return;
      if (e.key === 'ArrowRight') go(current + 1);
      if (e.key === 'ArrowLeft') go(current - 1);
    });
    const start = pages.findIndex(p => String(p.page) === App.params.get('page'));
    go(start >= 0 ? start : 0);
  }

  // 대화는 교안별 저장: summary + history(서버에 보냄) + log(화면 표시용)
  function Chat(doc, goPage) {
    const { t } = App;
    const key = `chat:${doc.doc_id}`;
    let state = { summary: null, history: [], log: [] };
    const box = h('div', { class: 'msgs' });
    const input = h('textarea', { rows: 3, placeholder: t.chatPlaceholder });
    const send = h('button', { type: 'button', class: 'btn' }, t.send);
    const clear = h('button', { type: 'button', class: 'link' }, t.clear);

    const bubble = m => h('div', { class: `msg ${m.role}` },
      h('p', {}, m.content),
      m.role === 'assistant' && m.citations ? h('div', { class: 'cites' },
        m.citations.length
          ? m.citations.map(c => h('button', { type: 'button', class: 'cite', title: c.quote || '', onclick: () => goPage(c.page) }, `${t.page} ${c.page}`))
          : h('span', { class: 'muted' }, t.noSource)) : null);
    const paint = () => {
      box.replaceChildren(...(state.log.length ? state.log.map(bubble) : [h('p', { class: 'muted hint' }, t.chatHint)]));
      box.scrollTop = box.scrollHeight;
    };

    async function ask() {
      const question = input.value.trim();
      if (!question || send.disabled) return;
      input.value = '';
      send.disabled = true;
      state.log.push({ role: 'user', content: question });
      paint();
      const waiting = h('div', { class: 'msg assistant pending' }, h('p', {}, t.thinking));
      box.append(waiting);
      box.scrollTop = box.scrollHeight;
      try {
        const res = await Api.chat({ anon_id: App.anonId, doc_id: doc.doc_id, lang: App.lang, question, summary: state.summary, history: state.history }, doc);
        // 이번 질문·답을 먼저 붙이고, 서버가 압축했으면 요약 교체 + 최근 kept개만 남김 (server/README.md 순서)
        state.history.push({ role: 'user', content: question }, { role: 'assistant', content: res.answer });
        if (res.summary != null) {
          state.summary = res.summary;
          state.history = state.history.slice(-(res.kept ?? 8));
        }
        state.log.push({ role: 'assistant', content: res.answer, citations: res.citations || [] });
      } catch (e) {
        state.log.push({ role: 'error', content: `${t.chatError} (${e.message})` });
      }
      state.log = state.log.slice(-200);
      await App.store.set({ [key]: state });
      send.disabled = false;
      paint();
      input.focus();
    }

    send.addEventListener('click', ask);
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        ask();
      }
    });
    clear.addEventListener('click', async () => {
      state = { summary: null, history: [], log: [] };
      await App.store.remove(key);
      paint();
    });
    App.store.get(key).then(r => {
      if (r[key]) state = { summary: null, history: [], log: [], ...r[key] };
      paint();
    });

    return h('aside', { class: 'chat' },
      h('div', { class: 'chat-head' }, h('strong', {}, t.chatTitle), clear),
      box,
      h('div', { class: 'composer' }, input, send));
  }

  return { render };
})();
