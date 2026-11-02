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

  // 번역문이 자리(box)에 맞을 때까지 글자를 줄임. 화면에서 MIN_PX보다 작게는 줄이지 않음
  // 그래도 넘치면 칸 안에서 자르고, 마우스를 올리거나 Tab으로 고르면 전체 문장을 보여 줌
  const MIN_PX = 10;
  function fit(el, size, scale = 1, code = false) {
    const min = MIN_PX / scale;
    let s = code ? size : Math.max(size, min); // 원래 작은 글도 최소 크기까지 키움 (코드 줄은 그대로)
    el.style.fontSize = `${s}px`;
    el.style.height = el.style.getPropertyValue('--h');
    el.style.marginTop = '';
    el.classList.remove('squeeze', 'more');
    el.removeAttribute('tabindex');
    el.removeAttribute('title');
    const over = () => el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
    const floor = code ? Math.min(size, Math.max(size * 0.45, min)) : Math.max(size * 0.45, min);
    while (s > floor && over()) {
      s = Math.max(floor, s * 0.92);
      el.style.fontSize = `${s}px`;
    }
    if (!over()) return;
    el.classList.add('squeeze'); // 긴 낱말도 줄바꿈
    if (!over()) return;
    el.classList.add('more');
    const lines = Math.max(1, Math.floor(el.clientHeight / (s * 1.15)));
    el.style.setProperty('--lines', lines);
    el.style.setProperty('--clip', `${lines * s * 1.15 + 1}px`);
    const need = lines * s * 1.15 + 1 - el.clientHeight;
    if (need > 0) { // 한 줄도 안 들어가는 낮은 칸: 위아래로 조금 넓혀 글자가 잘리지 않게
      el.style.height = `${el.clientHeight + need}px`;
      el.style.marginTop = `${-need / 2}px`;
    }
    el.tabIndex = 0;
    el.title = el.textContent;
  }

  // 서버 그림을 잠깐 못 받으면 두 번 더 시도
  function loadImage(img, url) {
    let tries = 0;
    img.onerror = () => {
      if (++tries > 2) return img.closest('.slide')?.classList.add('img-failed');
      setTimeout(() => (img.src = `${url}?retry=${tries}`), 600 * tries);
    };
    img.src = url;
  }

  // 슬라이드 한 장: 원본 그림, 또는 글자를 지운 배경 + 같은 자리에 번역문. 좌표 단위는 PDF pt
  function slide(doc, p, translated) {
    const [w, h0] = p.size || [720, 540];
    const inner = h('div', { class: 'slide-inner', style: `width:${w}px;height:${h0}px` });
    const bg = translated ? p.background : p.image;
    const img = h('img', { class: 'slide-bg', alt: '', draggable: 'false' });
    loadImage(img, imageUrl(doc, bg));
    inner.append(img);
    const boxes = [];
    const flows = [];
    if (translated) {
      const groups = new Map();
      for (const b of p.blocks) {
        if (b.group != null && b.gbox && b.translated) {
          if (!groups.has(b.group)) groups.set(b.group, []);
          groups.get(b.group).push(b);
          continue;
        }
        // codetext는 번역 단위일 뿐, 화면에는 번역문을 넣어 다시 쓴 codeline으로 표시
        if (!b.translated || b.translated === b.text || !b.box || b.type === 'codetext') continue;
        const [x0, y0, x1, y1] = b.box;
        const el = h('div', {
          class: `ov ${b.type} ${b.align || 'left'}${b.type === 'label' && !b.wrap ? ' nowrap' : ''}${b.ocr ? ' ocr' : ''}`,
          style: `left:${x0}px;top:${y0}px;width:${x1 - x0}px;height:${y1 - y0}px;--h:${y1 - y0}px;font-size:${b.size}px;` +
            `color:${b.color || '#000'};font-weight:${b.bold ? 700 : 400}${b.indent ? `;text-indent:${b.indent}px` : ''}`,
        }, clean(b.translated));
        inner.append(el);
        boxes.push([el, b.size, b.type === 'codeline' || b.type === 'comment']);
      }
      for (const items of groups.values()) {
        const el = flow(items);
        inner.append(el);
        flows.push(el);
      }
    }
    const box = h('div', { class: 'slide', 'data-w': w, 'data-h': h0, 'data-err': App.t.imageError }, inner);
    box.fitText = () => {
      const scale = Number(/scale\(([\d.]+)\)/.exec(inner.style.transform)?.[1]) || Number(inner.style.zoom) || 1;
      boxes.forEach(([el, size, code]) => fit(el, size, scale, code));
      flows.forEach(el => fitFlow(el, scale));
    };
    return box;
  }

  // 목록 묶음: 묶음 자리(gbox) 안에 항목을 위에서부터 쌓고, 글머리 기호는 원래 모양·색으로 다시 그림
  function flow(items) {
    const [gx0, gy0, gx1, gy1] = items[0].gbox; // 항목마다 자기 자리(box) 오른쪽 끝까지만 (옆 표·그림을 덮지 않게)
    const el = h('div', { class: 'ov flow', style: `left:${gx0}px;top:${gy0}px;width:${gx1 - gx0}px;height:${gy1 - gy0}px;--h:${gy1 - gy0}px` });
    let bottom = gy0;
    for (const b of [...items].sort((a, c) => a.rect[1] - c.rect[1])) {
      const [x0, y0, , y1] = b.rect;
      const center = b.align === 'center';
      // 기호가 둘째 줄 시작 자리에 걸치면 첫 줄은 기호 뒤에서 시작
      const indent = b.mark && !center ? Math.max(b.indent || 0, b.mark.rect[2] - x0 + b.size * 0.3) : b.indent || 0;
      const item = h('div', { class: `fi${center ? ' center' : ''}`, 'data-size': b.size, 'data-gap': Math.max(0, y0 - bottom),
        'data-indent': b.mark && !center && b.mark.rect[2] <= x0 ? b.indent || 0 : indent, style: `padding-left:${center ? 0 : x0 - gx0}px;padding-right:${center ? 0 : Math.max(0, gx1 - (b.box?.[2] ?? gx1))}px;` +
          `color:${b.color || '#000'};font-weight:${b.bold ? 700 : 400}` },
      clean(b.translated));
      if (b.mark) {
        item.prepend(h('span', { class: 'mk', 'data-size': b.mark.size, style: `color:${b.mark.color};${center ? '' : `left:${b.mark.rect[0] - gx0}px`}` },
          b.mark.char));
      }
      el.append(item);
      bottom = y1;
    }
    return el;
  }

  // 묶음 전체를 같은 비율로 줄여 맞춤 (가장 작은 글이 화면 MIN_PX보다 작아지지 않게)
  function fitFlow(el, scale) {
    const items = [...el.children];
    const sizes = items.map(x => Number(x.dataset.size));
    const min = MIN_PX / scale / Math.min(...sizes);
    const apply = f => items.forEach(x => {
      x.style.fontSize = `${x.dataset.size * f}px`;
      x.style.marginTop = `${x.dataset.gap * f}px`;
      x.style.textIndent = `${x.dataset.indent * f}px`;
      const mk = x.querySelector('.mk');
      if (mk) mk.style.fontSize = `${mk.dataset.size * f}px`;
    });
    el.classList.remove('more');
    el.removeAttribute('tabindex');
    let f = Math.max(1, min);
    apply(f);
    const over = () => el.scrollHeight > el.clientHeight + 1;
    while (over() && f > min) {
      f = Math.max(min, f * 0.94);
      apply(f);
    }
    if (over()) {
      el.classList.add('more'); // 그래도 넘치면 묶음 안에서 자르고 마우스·Tab으로 전체 보기
      el.tabIndex = 0;
    }
  }

  const slideReady = p => p.image && p.size;
  const overlaid = p => p.background && p.blocks?.some(b => b.translated && b.translated !== b.text);

  // 글 보기: 페이지 전체 번역 | 원문 (용어 표시). 한국어는 원문만
  function textView(p) {
    const { t } = App;
    const empty = !(p.original || '').trim();
    const or = h('article', { class: 'col original', lang: 'ko' }, h('h3', {}, t.original),
      empty ? h('p', { class: 'muted' }, t.noText) : highlighted(clean(p.original), p.terms || []));
    if (App.lang === 'ko') return h('div', { class: 'cols' }, or);
    return h('div', { class: 'cols side' },
      h('article', { class: 'col' }, h('h3', {}, t.translated),
        empty ? h('p', { class: 'muted' }, t.noText) : Object.assign(h('div', { class: 'text' }), { textContent: clean(p.translated) })),
      or);
  }

  // 번역 검사(서버 issues)를 쉬운 문장으로
  function issueText(s) {
    const { t } = App;
    const term = s.match(/^(?:KEEP 용어 누락|FIXED 번역어 미사용|BILINGUAL 원문 병기 누락): "([^"]+)"/);
    if (/^코드 자리표시/.test(s)) return t.issueCode;
    if (term) return t.issueTerm(term[1]);
    if (/번역되지 않은 한국어/.test(s)) return t.issueKorean;
    return `${t.issueOther}: ${s}`;
  }

  // 인쇄 → "PDF로 저장": 모든 페이지를 화면과 같은 슬라이드로 그려 A4 가로 한 장씩
  const PRINT = { w: 1060, h: 730 };
  async function printDoc(doc, translated) {
    const { t } = App;
    const note = h('div', { class: 'print-note' });
    const root = h('div', { class: 'print-root', 'aria-hidden': 'true' });
    document.body.append(root, note);
    const ready = doc.pages.filter(slideReady);
    const slides = ready.map(p => slide(doc, p, translated && overlaid(p)));
    // 슬라이드에 올리지 못한 페이지는 다음 장에 페이지 전체 번역
    root.append(...ready.flatMap((p, i) => [h('div', { class: 'print-page' }, slides[i]),
      translated && !overlaid(p) && (p.translated || '').trim() ? h('div', { class: 'print-page print-text' },
        h('p', { class: 'muted' }, `${t.pageNo(p.page)} · ${t.pageTextNote}`), Object.assign(h('div', { class: 'text' }), { textContent: clean(p.translated) })) : null]
      .filter(Boolean)));
    for (const el of slides) {
      const w = Number(el.dataset.w), hh = Number(el.dataset.h);
      const scale = Math.min(PRINT.w / w, PRINT.h / hh);
      el.style.width = `${w * scale}px`;
      el.style.height = `${hh * scale}px`;
      el.firstChild.style.zoom = scale; // 인쇄는 zoom: transform이면 크롬이 페이지를 넓게 보고 전체를 줄임
    }
    slides.forEach(el => el.fitText());
    const imgs = [...root.querySelectorAll('img')];
    let done = 0;
    note.textContent = `${t.converting} ${t.preparingPdf(0, imgs.length)}`;
    await Promise.all(imgs.map(img => (img.complete && img.naturalWidth ? Promise.resolve() : new Promise(r => {
      img.addEventListener('load', r, { once: true });
      img.addEventListener('error', r, { once: true });
    })).then(() => (note.textContent = `${t.converting} ${t.preparingPdf(++done, imgs.length)}`))));
    note.remove();
    const cleanup = () => root.remove();
    addEventListener('afterprint', cleanup, { once: true });
    print();
  }

  // 교안 안에서 복습: 범위(이 쪽·전체·직접) · 유형 · 문제 수 → 같은 탭에서 복습 화면으로
  function QuizPanel(doc, currentPage, onClose) {
    const { t } = App;
    const first = doc.pages[0].page;
    const last = doc.pages[doc.pages.length - 1].page;
    const picked = { multiple: true, short: true, matching: false };
    let range = 'page';
    const num = label => h('input', { type: 'number', min: first, max: last, class: 'num', 'aria-label': label });
    const from = num(t.rangeFrom);
    const to = num(t.rangeTo);
    const custom = h('div', { class: 'range-row', hidden: true }, h('label', {}, t.rangeFrom, from), '~', h('label', {}, t.rangeTo, to));
    const rangeBtns = [['page', t.thisPage], ['all', t.allPages], ['custom', t.custom]].map(([k, label]) =>
      h('button', { type: 'button', 'data-r': k, onclick: () => { range = k; paint(); } }, label));
    const chips = Object.entries(t.types).map(([k, label]) => h('button', { type: 'button', class: `chip-t${picked[k] ? ' on' : ''}`,
      onclick: e => {
        picked[k] = !picked[k];
        if (!Object.values(picked).some(Boolean)) picked[k] = true;
        e.currentTarget.classList.toggle('on', picked[k]);
      } }, label));
    const count = h('select', { class: 'count' }, [5, 10, 15, 20].map(n => h('option', { value: n }, String(n))));
    const clamp = el => Math.max(first, Math.min(last, Number(el.value) || currentPage()));

    function paint() {
      rangeBtns.forEach(b => b.classList.toggle('on', b.dataset.r === range));
      custom.hidden = range !== 'custom';
      if (range === 'custom' && !from.value) from.value = to.value = currentPage();
    }
    function start() {
      let [a, b] = range === 'page' ? [currentPage(), currentPage()] : [clamp(from), clamp(to)];
      if (a > b) [a, b] = [b, a];
      const q = new URLSearchParams(App.params);
      q.delete('page');
      q.set('mode', 'review');
      q.set('types', Object.keys(picked).filter(k => picked[k]).join(','));
      q.set('count', count.value);
      if (range === 'all') q.delete('pages');
      else q.set('pages', `${a}-${b}`);
      q.set('from', currentPage());
      location.href = `viewer.html?${q}`;
    }
    paint();
    return h('aside', { class: 'side-panel quiz-panel' }, h('div', { class: 'panel-inner' },
      h('div', { class: 'chat-head' }, h('strong', {}, t.quizTitle), h('span', { class: 'grow' }),
        h('button', { type: 'button', class: 'icon', 'aria-label': t.close, title: t.close, onclick: () => onClose() }, '×')),
      h('div', { class: 'quiz-form' },
        h('p', { class: 'muted' }, t.range), h('div', { class: 'seg' }, rangeBtns), custom,
        h('p', { class: 'muted' }, t.questionTypes), h('div', { class: 'chips' }, chips),
        h('label', { class: 'count-row' }, t.count, count),
        h('button', { type: 'button', class: 'btn', onclick: start }, t.start))));
  }

  function render(doc, { autoSave = false } = {}) {
    const { t } = App;
    const app = document.getElementById('app');
    const pages = doc.pages;
    let current = 0;
    const korean = App.lang === 'ko'; // 한국어: 번역 없이 원문
    let mode = !pages.some(slideReady) ? 'text' : korean ? 'or' : 'tr';

    const pick = h('select', { class: 'page-pick', 'aria-label': t.pages, onchange: e => go(Number(e.target.value)) },
      pages.map((p, i) => h('option', { value: i }, `${t.pageOf(p.page, pages[pages.length - 1].page)}${p.issues?.length ? '  !' : ''}`)));
    const prev = h('button', { type: 'button', class: 'icon', title: t.prev, 'aria-label': t.prev, onclick: () => go(current - 1) }, '‹');
    const next = h('button', { type: 'button', class: 'icon', title: t.next, 'aria-label': t.next, onclick: () => go(current + 1) }, '›');
    const modes = (korean ? [['or', t.original], ['text', t.textMode]] : [['tr', t.translated], ['or', t.original], ['side', t.side], ['text', t.textMode]]).map(([k, label]) =>
      h('button', { type: 'button', 'data-m': k, onclick: () => ((mode = k), paint()) }, label));
    const issues = h('div', { class: 'issue-pop', hidden: true });
    const warn = h('button', { type: 'button', class: 'warn-badge', title: t.issuePage, 'aria-label': t.issuePage, hidden: true,
      onclick: e => { e.stopPropagation(); issues.hidden = !issues.hidden; } }, '!');
    document.addEventListener('click', e => !issues.contains(e.target) && (issues.hidden = true));
    const review = doc.needs_review?.length ? h('span', { class: 'warn-badge soft', title: t.needsReview }, 'i') : null;
    const stage = h('div', { class: 'stage' });
    const chat = Chat(doc, n => goPage(n), () => toggle('chat', false));
    const chatBtn = h('button', { type: 'button', class: 'chat-toggle', 'aria-expanded': 'false', onclick: () => toggle('chat') }, t.chatOpen);
    const quiz = QuizPanel(doc, () => pages[current].page, () => toggle('quiz', false));
    const quizBtn = h('button', { type: 'button', class: 'tool-btn quiz-toggle', 'aria-expanded': 'false', onclick: () => toggle('quiz') }, t.quizOpen);
    const saveBtn = h('button', { type: 'button', class: 'tool-btn save-pdf', title: t.savePdf,
      onclick: () => printDoc(doc, !korean && mode !== 'or') }, t.savePdf);
    // 왼쪽 페이지 목록: 열면 슬라이드를 옆으로 밀고 들어옴 (GitHub 파일 목록처럼)
    // 목록 제목: 여러 페이지에 반복되는 단원 제목 대신 그 페이지의 첫 항목
    const headings = {};
    pages.forEach(p => p.blocks?.filter(x => x.type === 'heading').forEach(x => (headings[x.text] = (headings[x.text] || 0) + 1)));
    const titleOf = p => {
      const words = (p.blocks || []).filter(x => !['code', 'comment', 'label'].includes(x.type));
      const b = words.find(x => x.type !== 'heading' || headings[x.text] < 3) || words[0];
      return clean(b ? b.translated || b.text : (p.translated || '').split('\n')[0]).slice(0, 80);
    };
    const items = pages.map((p, i) => h('button', { type: 'button', class: 'pg', 'data-i': i, title: titleOf(p), onclick: () => go(i) },
      h('span', { class: 'pg-num' }, String(p.page)), h('span', { class: 'pg-title' }, titleOf(p)),
      p.issues?.length ? h('span', { class: 'warn-dot', title: t.issuePage }, '!') : null));
    const list = h('aside', { class: 'side-panel pages-panel', 'aria-label': t.pages },
      h('div', { class: 'panel-inner' }, h('div', { class: 'panel-head' }, h('strong', {}, t.pages),
        h('button', { type: 'button', class: 'icon', 'aria-label': t.close, title: t.close, onclick: () => toggle('pages', false) }, '×')),
      h('nav', { class: 'pg-list' }, items)));
    const listBtn = h('button', { type: 'button', class: 'icon panel-toggle', title: t.pages, 'aria-label': t.pages, 'aria-expanded': 'false', onclick: () => toggle('pages') });
    listBtn.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M6 3v10" stroke="currentColor" stroke-width="1.4"/></svg>';
    const panels = { pages: [list, listBtn], chat: [chat, chatBtn], quiz: [quiz, quizBtn] };

    function toggle(name, open) {
      const [panel, btn] = panels[name];
      open = open ?? !panel.classList.contains('open');
      if (open && (name === 'chat' || name === 'quiz')) toggle(name === 'chat' ? 'quiz' : 'chat', false); // 오른쪽 패널은 하나만
      panel.classList.toggle('open', open);
      btn.setAttribute('aria-expanded', String(open));
      btn.classList.toggle('on', open);
      try { localStorage.setItem(`panel:${name}`, open ? '1' : ''); } catch {}
      if (open && name === 'chat') chat.querySelector('textarea')?.focus();
      if (open && name === 'pages') list.querySelector('.pg.on')?.scrollIntoView({ block: 'nearest' });
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
        el.fitText();
      }
    }

    function paint() {
      const p = pages[current];
      pick.value = String(current);
      items.forEach((b, i) => b.classList.toggle('on', i === current));
      items[current].scrollIntoView({ block: 'nearest' });
      modes.forEach(b => b.classList.toggle('on', b.dataset.m === mode));
      modes.forEach(b => (b.hidden = b.dataset.m !== 'text' && !slideReady(p)));
      prev.disabled = current === 0;
      next.disabled = current === pages.length - 1;
      warn.hidden = !p.issues?.length;
      issues.hidden = true;
      issues.replaceChildren(h('strong', {}, t.issueTitle), h('ul', {}, [...new Set((p.issues || []).map(issueText))].map(x => h('li', {}, x))));
      const m = slideReady(p) ? mode : 'text';
      stage.className = `stage ${m}`;
      if (m === 'text') {
        stage.replaceChildren(textView(p));
        stage.scrollTop = 0;
        return;
      }
      // 덩어리별 번역이 없는 페이지: 원본 슬라이드 + 페이지 전체 번역
      const fallback = !korean && m !== 'or' && !overlaid(p) && (p.translated || '').trim();
      const tr = overlaid(p) ? slide(doc, p, true) : slide(doc, p, false);
      const or = slide(doc, p, false);
      const shown = m === 'side' ? [tr, or] : m === 'tr' ? [tr] : [or];
      stage.replaceChildren(h('div', { class: 'slides' }, shown), ...(fallback ? [h('div', { class: 'page-text' },
        h('p', { class: 'muted' }, t.pageTextNote), Object.assign(h('div', { class: 'text' }), { textContent: clean(p.translated) }))] : []));
      layout();
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
      h('nav', { class: 'crumbs' }, listBtn, prev, pick, next, h('span', { class: 'issue-wrap' }, warn, issues), review),
      h('div', { class: 'seg' }, modes),
      saveBtn, quizBtn, chatBtn);
    document.querySelector('.bar .toolbar')?.remove();
    document.querySelector('.bar .titles').after(toolbar); // 제목과 같은 줄: 슬라이드 자리를 넓힘
    app.replaceChildren(h('div', { class: 'doc' }, h('div', { class: 'doc-body' }, list, stage, chat, quiz)));
    new ResizeObserver(() => layout()).observe(stage);
    for (const name of ['pages', 'chat']) {
      try { if (localStorage.getItem(`panel:${name}`)) toggle(name, true); } catch {}
    }
    addEventListener('keydown', e => {
      if (e.target.closest('textarea, input, select')) return;
      if (e.key === 'ArrowRight') go(current + 1);
      if (e.key === 'ArrowLeft') go(current - 1);
      if (e.key === 'Escape') { toggle('chat', false); toggle('quiz', false); issues.hidden = true; }
    });
    const start = pages.findIndex(p => String(p.page) === App.params.get('page'));
    go(start >= 0 ? start : 0);
    if (autoSave) printDoc(doc, !korean);
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
          ? m.citations.map(c => h('button', { type: 'button', class: 'cite', title: c.quote || '', onclick: () => goPage(c.page) }, t.pageNo(c.page)))
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
    return h('aside', { class: 'side-panel chat' }, h('div', { class: 'panel-inner' },
      h('div', { class: 'chat-head' }, h('strong', {}, t.chatTitle), h('span', { class: 'grow' }), clear, close),
      box,
      h('div', { class: 'composer' }, input, send)));
  }

  return { render };
})();
