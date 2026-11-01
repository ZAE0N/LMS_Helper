// 교안 뷰어: 슬라이드(원본 모양 + 번역) · 상단 페이지 막대 · 챗봇 서랍
const DocView = (() => {
  const { h } = App;
  const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // PDF 글머리표(사용자 정의 문자 U+E000~F8FF)는 글꼴이 없어 □로 보이므로 •로 표시
  const clean = s => (s || '').replace(/[\uE000-\uF8FF]/g, '• ');

  // 원문에서 용어집 용어를 <mark>로 표시
  function highlighted(text, terms, box = h('div', { class: 'text' })) {
    if (!terms.length) return (box.textContent = text), box;
    const re = new RegExp(`(${[...terms].sort((a, b) => b.length - a.length).map(escapeRe).join('|')})`, 'g');
    text.split(re).forEach((part, i) => box.append(i % 2 ? h('mark', {}, part) : part));
    return box;
  }

  const imageUrl = (doc, name) => `${CONFIG.SERVER_URL}/documents/${doc.doc_id}/figures/${name}`;

  // 번역문이 자리(box)에 맞을 때까지 글자를 줄임 (최소 원래 크기의 45%)
  function fit(el, size) {
    let s = size;
    while (s > size * 0.45 && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) {
      s *= 0.92;
      el.style.fontSize = `${s}px`;
    }
  }

  // 슬라이드 한 장: 원본 그림, 또는 글자를 지운 배경 + 같은 자리에 번역문. 좌표 단위는 PDF pt
  function slide(doc, p, translated) {
    const [w, h0] = p.size || [720, 540];
    const inner = h('div', { class: 'slide-inner', style: `width:${w}px;height:${h0}px` });
    const bg = translated ? p.background : p.image;
    inner.append(h('img', { class: 'slide-bg', alt: '', src: imageUrl(doc, bg), draggable: 'false' }));
    const boxes = [];
    if (translated) {
      for (const b of p.blocks) {
        if (!b.translated || b.translated === b.text || !b.box) continue;
        const [x0, y0, x1, y1] = b.box;
        const el = h('div', {
          class: `ov ${b.type}${b.align === 'center' ? ' center' : ''}${b.ocr ? ' ocr' : ''}`,
          style: `left:${x0}px;top:${y0}px;width:${x1 - x0}px;height:${y1 - y0}px;font-size:${b.size}px;` +
            `color:${b.color || '#000'};font-weight:${b.bold ? 700 : 400}`,
        }, clean(b.translated));
        inner.append(el);
        boxes.push([el, b.size]);
      }
    }
    const box = h('div', { class: 'slide', 'data-w': w, 'data-h': h0 }, inner);
    box.fitText = () => boxes.forEach(([el, size]) => fit(el, size));
    return box;
  }

  const slideReady = p => p.image && p.size;
  const overlaid = p => p.background && p.blocks?.some(b => b.translated && b.translated !== b.text);

  // 글 보기: 페이지 전체 번역 | 원문 (용어 표시)
  function textView(p) {
    const { t } = App;
    const empty = !(p.original || '').trim();
    return h('div', { class: 'cols side' },
      h('article', { class: 'col' }, h('h3', {}, t.translated),
        empty ? h('p', { class: 'muted' }, t.noText) : Object.assign(h('div', { class: 'text' }), { textContent: clean(p.translated) })),
      h('article', { class: 'col original', lang: 'ko' }, h('h3', {}, t.original),
        empty ? h('p', { class: 'muted' }, t.noText) : highlighted(clean(p.original), p.terms || [])));
  }

  function render(doc) {
    const { t } = App;
    const app = document.getElementById('app');
    const pages = doc.pages;
    let current = 0;
    let mode = pages.some(slideReady) ? 'tr' : 'text';

    const pick = h('select', { class: 'page-pick', 'aria-label': t.pages, onchange: e => go(Number(e.target.value)) },
      pages.map((p, i) => h('option', { value: i }, `${t.page} ${p.page} / ${pages[pages.length - 1].page}${p.issues?.length ? '  !' : ''}`)));
    const prev = h('button', { type: 'button', class: 'icon', title: t.prev, 'aria-label': t.prev, onclick: () => go(current - 1) }, '‹');
    const next = h('button', { type: 'button', class: 'icon', title: t.next, 'aria-label': t.next, onclick: () => go(current + 1) }, '›');
    const modes = [['tr', t.translated], ['or', t.original], ['side', t.side], ['text', t.textMode]].map(([k, label]) =>
      h('button', { type: 'button', 'data-m': k, onclick: () => ((mode = k), paint()) }, label));
    const warn = h('span', { class: 'warn-badge', title: t.issuePage, hidden: true }, '!');
    const review = doc.needs_review?.length ? h('span', { class: 'warn-badge soft', title: t.needsReview }, 'i') : null;
    const stage = h('div', { class: 'stage' });
    const chat = Chat(doc, n => goPage(n), () => toggleChat(false));
    const chatBtn = h('button', { type: 'button', class: 'chat-toggle', 'aria-expanded': 'false', onclick: () => toggleChat() }, `💬 ${t.chatOpen}`);

    function toggleChat(open = !chat.classList.contains('open')) {
      chat.classList.toggle('open', open);
      chatBtn.setAttribute('aria-expanded', String(open));
      chatBtn.classList.toggle('on', open);
      if (open) chat.querySelector('textarea')?.focus();
    }

    // 화면 크기에 맞춰 슬라이드 크기를 정함 (모든 페이지 같은 크기, 원본 비율 유지)
    function layout() {
      const slides = [...stage.querySelectorAll('.slide')];
      if (!slides.length) return;
      const gap = 16;
      const cw = (stage.clientWidth - 24 - gap * (slides.length - 1)) / slides.length;
      const ch = stage.clientHeight - 24;
      for (const el of slides) {
        const w = Number(el.dataset.w), hh = Number(el.dataset.h);
        const scale = Math.max(0.1, Math.min(cw / w, ch / hh));
        el.style.width = `${w * scale}px`;
        el.style.height = `${hh * scale}px`;
        el.firstChild.style.transform = `scale(${scale})`;
      }
    }

    function paint() {
      const p = pages[current];
      pick.value = String(current);
      modes.forEach(b => b.classList.toggle('on', b.dataset.m === mode));
      modes.forEach(b => (b.hidden = b.dataset.m !== 'text' && !slideReady(p)));
      prev.disabled = current === 0;
      next.disabled = current === pages.length - 1;
      warn.hidden = !p.issues?.length;
      const m = slideReady(p) ? mode : 'text';
      stage.className = `stage ${m}`;
      if (m === 'text') {
        stage.replaceChildren(textView(p));
        stage.scrollTop = 0;
        return;
      }
      // 덩어리별 번역이 없는 페이지: 원본 슬라이드 + 페이지 전체 번역
      const fallback = m !== 'or' && !overlaid(p) && (p.translated || '').trim();
      const tr = overlaid(p) ? slide(doc, p, true) : slide(doc, p, false);
      const or = slide(doc, p, false);
      const shown = m === 'side' ? [tr, or] : m === 'tr' ? [tr] : [or];
      stage.replaceChildren(h('div', { class: 'slides' }, shown),
        fallback ? h('div', { class: 'page-text' }, h('p', { class: 'muted' }, t.pageTextNote),
          Object.assign(h('div', { class: 'text' }), { textContent: clean(p.translated) })) : null);
      layout();
      shown.forEach(el => el.fitText());
      // 다음 페이지 그림 미리 받기
      const n = pages[current + 1];
      if (n?.image) [n.image, n.background].filter(Boolean).forEach(x => (new Image().src = imageUrl(doc, x)));
    }
    function go(i) {
      current = Math.max(0, Math.min(pages.length - 1, i));
      paint();
    }
    const goPage = n => go(pages.findIndex(p => p.page === n));

    const toolbar = h('div', { class: 'toolbar' },
      h('nav', { class: 'crumbs' }, prev, pick, next, warn, review),
      h('div', { class: 'seg' }, modes),
      chatBtn);
    app.replaceChildren(h('div', { class: 'doc' }, toolbar, h('div', { class: 'doc-body' }, stage, chat)));
    new ResizeObserver(() => layout()).observe(stage);
    addEventListener('keydown', e => {
      if (e.target.closest('textarea, input, select')) return;
      if (e.key === 'ArrowRight') go(current + 1);
      if (e.key === 'ArrowLeft') go(current - 1);
      if (e.key === 'Escape') toggleChat(false);
    });
    const start = pages.findIndex(p => String(p.page) === App.params.get('page'));
    go(start >= 0 ? start : 0);
  }

  // 대화는 교안별 저장: summary + history(서버에 보냄) + log(화면 표시용)
  function Chat(doc, goPage, onClose) {
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

    const close = h('button', { type: 'button', class: 'icon', 'aria-label': t.close, title: t.close,
      onclick: () => onClose() }, '×');
    return h('aside', { class: 'chat' },
      h('div', { class: 'chat-head' }, h('strong', {}, t.chatTitle), h('span', { class: 'grow' }), clear, close),
      box,
      h('div', { class: 'composer' }, input, send));
  }

  return { render };
})();
