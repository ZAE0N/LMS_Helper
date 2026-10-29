// LMS 모든 페이지 오른쪽 끝의 서랍 패널 (알림·교안·복습). 문구는 한국어, 번역은 크롬 번역이 함
(() => {
  if (window !== top || QUIZ_PATH.test(location.pathname)) return;

  const KO = {
    brand: 'LMS Helper',
    tabs: { notices: '🔔 알림', materials: '📄 교안', review: '🧠 복습' },
    close: '닫기',
    sources: { course: '수업', school: '학교', dept: '학과' },
    more: '더 보기',
    todo: '할 일',
    deadline: '마감',
    closed: '마감됨',
    today: '오늘 마감',
    original: '원문 보기',
    empty: '공지가 없습니다',
    loading: '불러오는 중…',
    error: '불러오지 못했습니다',
    sample: '예시 데이터',
    open: '열기',
    review: '복습',
    noFiles: '올라온 교안이 없습니다',
    noCourses: '수강 중인 과목이 없습니다',
    filesCount: n => `교안 ${n}개`,
    reviewFor: '복습할 교안',
    types: { multiple: '객관식', short: '주관식', matching: '연결형' },
    count: n => `${n}문제`,
    start: '복습 시작',
    lastResult: '최근 결과',
    weak: '약한 부분',
    noRecord: '아직 복습 기록이 없습니다'
  };
  const FILE_CACHE_MS = 10 * 60 * 1000;

  // ---------- 도우미 ----------
  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c);
    return el;
  }
  const bg = msg => chrome.runtime.sendMessage(msg).then(r => {
    if (!r?.ok) throw new Error(r?.error || 'server');
    return r.data;
  });
  const Api = {
    getNotices: q => (CONFIG.USE_MOCK ? Promise.resolve(MOCK.notices(q)) : bg({ type: 'api', path: `/notices?${new URLSearchParams(q)}` })),
    extract: items =>
      CONFIG.USE_MOCK ? Promise.resolve(MOCK.extract({ items })) : bg({ type: 'api', method: 'POST', path: '/notices/extract', body: { items } })
  };
  const openPage = page => chrome.runtime.sendMessage({ type: 'open-page', page });
  const store = {
    get: k => chrome.storage.local.get(k).catch(() => ({})),
    set: o => chrome.storage.local.set(o).catch(() => {})
  };

  function dday(deadline) {
    if (!deadline) return null;
    const [y, m, d] = deadline.split('-').map(Number);
    const now = new Date();
    const diff = Math.round((new Date(y, m - 1, d) - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
    if (diff < 0) return { text: KO.closed, level: 'past' };
    if (diff === 0) return { text: KO.today, level: 'urgent' };
    return { text: `D-${diff}`, level: diff <= 3 ? 'urgent' : diff <= 7 ? 'soon' : 'later' };
  }
  const fmtDate = s => {
    if (!s) return '';
    const d = new Date(s);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  };
  // 같은 공지(여러 분반에 같이 올라온 것 포함)는 하나로
  function dedupe(list) {
    const seen = new Set();
    return list.filter(n => {
      const content = `${n.source}|${n.title}|${(n.body || n.summary || '').slice(0, 120)}`;
      if (seen.has(n.id) || seen.has(content)) return false;
      seen.add(n.id);
      seen.add(content);
      return true;
    });
  }

  // ---------- Canvas 데이터 ----------
  let coursesP = null;
  const getCourses = () =>
    (coursesP ??= canvasGet('/api/v1/courses?enrollment_state=active&per_page=50').then(cs =>
      cs.map(c => ({ id: c.id, name: shortCourse(c.name) })).sort((a, b) => a.name.localeCompare(b.name, 'ko'))
    ));

  // 파일 탭 → 막혀 있으면 모듈 경유
  async function getCourseFiles(course) {
    const key = `lmsh:files:${course.id}`;
    try {
      const c = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (c && Date.now() - c.at < FILE_CACHE_MS) return c.items;
    } catch {}
    let items = [];
    try {
      const q = new URLSearchParams({ sort: 'created_at', order: 'desc', per_page: '50' });
      q.append('content_types[]', 'application/pdf');
      items = (await canvasGet(`/api/v1/courses/${course.id}/files?${q}`)).map(f => ({ id: f.id, name: f.display_name, created_at: f.created_at }));
    } catch {
      try {
        const mods = await canvasGet(`/api/v1/courses/${course.id}/modules?include[]=items&per_page=50`);
        items = mods.flatMap(m => m.items || [])
          .filter(i => i.type === 'File' && /\.pdf$/i.test(i.title || ''))
          .map(i => ({ id: i.content_id, name: i.title, created_at: null }));
      } catch {}
    }
    items = items.map(f => ({ ...f, courseId: course.id, course: course.name }));
    try { sessionStorage.setItem(key, JSON.stringify({ at: Date.now(), items })); } catch {}
    return items;
  }

  const viewerUrl = (f, extra = {}) =>
    `viewer.html?${new URLSearchParams({ file: f.id, course: f.courseId, name: f.name, courseName: f.course, ...extra })}`;

  // ---------- 탭: 알림 ----------
  function noticesTab() {
    const state = { source: 'course', limit: CONFIG.LIMITS[0], page: 1, items: [], seq: 0 };
    const list = h('div', { class: 'lmsh-list' });
    const status = h('p', { class: 'lmsh-status' });
    const more = h('button', { class: 'lmsh-more', type: 'button', hidden: true, onclick: () => load(false) }, KO.more);
    const seg = Object.entries(KO.sources).map(([key, label]) =>
      h('button', { type: 'button', 'data-s': key, onclick: () => ((state.source = key), load(true)) }, label));
    const limit = h('select', { class: 'lmsh-select-sm', onchange: e => ((state.limit = Number(e.target.value)), load(true)) },
      CONFIG.LIMITS.map(n => h('option', { value: n }, String(n))));

    const row = n => {
      const d = dday(n.deadline);
      return h('details', { class: 'lmsh-notice' },
        h('summary', {},
          h('span', { class: `lmsh-dday ${d ? d.level : 'none'}` }, d ? d.text : ''),
          h('div', { class: 'lmsh-main' },
            h('strong', { class: 'lmsh-title' }, n.title),
            n.action ? h('span', { class: 'lmsh-todo' }, `${KO.todo}: ${n.action}`) : n.summary ? h('span', { class: 'lmsh-sub' }, n.summary) : null),
          h('div', { class: 'lmsh-side' },
            n.course ? h('span', { class: 'lmsh-course' }, n.course) : null,
            n.posted_at ? h('span', { class: 'lmsh-date' }, fmtDate(n.posted_at)) : null)),
        h('div', { class: 'lmsh-detail' },
          n.body ? h('p', { class: 'lmsh-text' }, n.body) : null,
          n.deadline ? h('p', { class: 'lmsh-meta' }, `${KO.deadline}: ${n.deadline_text || n.deadline}`) : null,
          n.url?.startsWith('https://') ? h('a', { href: n.url, target: '_blank', rel: 'noopener noreferrer', class: 'lmsh-link' }, `${KO.original} ↗`) : null));
    };

    async function courseNotices(page) {
      const raw = await getAnnouncements({ limit: state.limit, page });
      const key = it => `ex:${it.id}:${it.updated_at}`;
      const cached = await store.get(raw.items.map(key));
      const todo = raw.items.filter(it => !cached[key(it)]);
      if (todo.length) {
        const { items } = await Api.extract(todo.map(({ id, title, body }) => ({ id, title, body })));
        const fresh = {};
        items.forEach(x => {
          const src = todo.find(t => t.id === x.id);
          if (src) fresh[key(src)] = x;
        });
        Object.assign(cached, fresh);
        if (!CONFIG.USE_MOCK) store.set(fresh);
      }
      return {
        items: raw.items.map(it => ({ ...it, summary: null, deadline: null, deadline_text: null, action: null, ...(cached[key(it)] || {}), id: it.id })),
        has_more: raw.has_more
      };
    }

    async function load(reset) {
      const seq = ++state.seq;
      if (reset) {
        state.page = 1;
        state.items = [];
        list.replaceChildren();
      } else state.page += 1;
      seg.forEach(b => b.classList.toggle('on', b.dataset.s === state.source));
      status.textContent = KO.loading;
      more.hidden = true;
      try {
        const res = state.source === 'course'
          ? await courseNotices(state.page)
          : await Api.getNotices({ source: state.source, limit: state.limit, page: state.page });
        if (seq !== state.seq) return;
        const before = state.items.length;
        state.items = dedupe(state.items.concat(res.items));
        list.replaceChildren(...state.items.map(row));
        status.textContent = state.items.length ? '' : KO.empty;
        more.hidden = !res.has_more || state.items.length === before;
      } catch (e) {
        if (seq === state.seq) status.textContent = `${KO.error} (${e.message})`;
      }
    }

    const el = h('div', { class: 'lmsh-tab' },
      h('div', { class: 'lmsh-toolbar' }, h('div', { class: 'lmsh-seg' }, seg), limit,
        CONFIG.USE_MOCK ? h('span', { class: 'lmsh-sample' }, KO.sample) : null),
      list, status, more);
    return { el, start: () => load(true) };
  }

  // ---------- 탭: 교안 (과목별) ----------
  function materialsTab(onReview) {
    const list = h('div', { class: 'lmsh-courses' }, h('p', { class: 'lmsh-status' }, KO.loading));

    const fileRow = f => h('div', { class: 'lmsh-file' },
      h('span', { class: 'lmsh-pdf' }, 'PDF'),
      h('div', { class: 'lmsh-main' },
        h('strong', { class: 'lmsh-title' }, f.name),
        f.created_at ? h('span', { class: 'lmsh-sub' }, fmtDate(f.created_at)) : null),
      h('button', { type: 'button', class: 'lmsh-btn ghost', onclick: () => onReview(f) }, KO.review),
      h('button', { type: 'button', class: 'lmsh-btn', onclick: () => openPage(viewerUrl(f)) }, KO.open));

    function courseBlock(course) {
      const count = h('span', { class: 'lmsh-count' });
      const body = h('div', { class: 'lmsh-files' });
      let loaded = false;
      const box = h('details', { class: 'lmsh-course-box', ontoggle: async () => {
        if (!box.open || loaded) return;
        loaded = true;
        body.replaceChildren(h('p', { class: 'lmsh-status' }, KO.loading));
        const files = await getCourseFiles(course).catch(() => []);
        count.textContent = KO.filesCount(files.length);
        body.replaceChildren(...(files.length ? files.map(fileRow) : [h('p', { class: 'lmsh-status' }, KO.noFiles)]));
      } },
        h('summary', {}, h('strong', {}, course.name), count),
        body);
      return box;
    }

    const start = () =>
      getCourses()
        .then(cs => list.replaceChildren(...(cs.length ? cs.map(courseBlock) : [h('p', { class: 'lmsh-status' }, KO.noCourses)])))
        .catch(e => list.replaceChildren(h('p', { class: 'lmsh-status' }, `${KO.error} (${e.message})`)));
    return { el: h('div', { class: 'lmsh-tab' }, list), start };
  }

  // ---------- 탭: 복습 ----------
  function reviewTab() {
    const picked = { multiple: true, short: true, matching: false };
    let files = [];
    const select = h('select', { class: 'lmsh-select' }, h('option', {}, KO.loading));
    const count = h('select', { class: 'lmsh-select-sm' }, [5, 10].map(n => h('option', { value: n }, KO.count(n))));
    const chips = Object.entries(KO.types).map(([k, label]) =>
      h('button', { type: 'button', class: `lmsh-chip${picked[k] ? ' on' : ''}`, onclick: e => {
        picked[k] = !picked[k];
        if (!Object.values(picked).some(Boolean)) picked[k] = true;
        e.currentTarget.classList.toggle('on', picked[k]);
      } }, label));
    const go = h('button', { type: 'button', class: 'lmsh-btn wide', disabled: true, onclick: () => {
      const f = files.find(x => String(x.id) === select.value);
      if (!f) return;
      openPage(viewerUrl(f, { mode: 'review', types: Object.keys(picked).filter(k => picked[k]).join(','), count: count.value }));
    } }, KO.start);
    const record = h('p', { class: 'lmsh-sub' }, KO.noRecord);
    let wanted = null;

    async function start() {
      const cs = await getCourses().catch(() => []);
      const groups = await Promise.all(cs.map(async c => ({ c, files: await getCourseFiles(c).catch(() => []) })));
      files = groups.flatMap(g => g.files);
      const opts = groups.filter(g => g.files.length).map(g =>
        h('optgroup', { label: g.c.name }, g.files.map(f => h('option', { value: f.id }, f.name))));
      select.replaceChildren(...(opts.length ? opts : [h('option', {}, KO.noFiles)]));
      go.disabled = !files.length;
      if (wanted) select.value = String(wanted.id);
      const { lastQuiz } = await store.get('lastQuiz');
      if (lastQuiz) record.textContent = `${KO.lastResult} ${lastQuiz.score}/${lastQuiz.total}${lastQuiz.weakPage ? ` · ${KO.weak}: ${lastQuiz.weakPage}쪽` : ''}`;
    }

    const el = h('div', { class: 'lmsh-tab' },
      h('label', { class: 'lmsh-label' }, KO.reviewFor), select,
      h('div', { class: 'lmsh-chips' }, chips, count),
      go, record);
    return { el, start, preselect: f => { wanted = f; if (files.length) select.value = String(f.id); } };
  }

  // ---------- 서랍 ----------
  let ui = null;
  function build() {
    const review = reviewTab();
    const tabs = {
      notices: noticesTab(),
      materials: materialsTab(f => { review.preselect(f); show('review'); }),
      review
    };
    const started = new Set();
    const tabBtns = Object.entries(KO.tabs).map(([k, label]) => h('button', { type: 'button', 'data-t': k, onclick: () => show(k) }, label));
    const body = h('div', { class: 'lmsh-body' }, Object.values(tabs).map(t => t.el));

    function show(k) {
      tabBtns.forEach(b => b.classList.toggle('on', b.dataset.t === k));
      Object.entries(tabs).forEach(([key, t]) => (t.el.hidden = key !== k));
      if (!started.has(k)) {
        started.add(k);
        tabs[k].start();
      }
      store.set({ drawerTab: k });
    }

    const drawer = h('aside', { class: 'lmsh-drawer', 'aria-label': KO.brand },
      h('header', { class: 'lmsh-top' },
        h('strong', { class: 'lmsh-brand' }, KO.brand),
        h('nav', { class: 'lmsh-tabs' }, tabBtns),
        h('button', { type: 'button', class: 'lmsh-x', 'aria-label': KO.close, onclick: () => toggle(false) }, '×')),
      body);
    const handle = h('button', { type: 'button', class: 'lmsh-handle', onclick: () => toggle() }, KO.brand);
    const root = h('div', { class: 'lmsh-ui' }, handle, drawer);
    document.body.append(root);
    return { root, drawer, show, firstShown: false };
  }

  async function toggle(force) {
    ui ??= build();
    const open = force ?? !ui.root.classList.contains('open');
    ui.root.classList.toggle('open', open);
    document.documentElement.classList.toggle('lmsh-drawer-open', open);
    if (open && !ui.firstShown) {
      ui.firstShown = true;
      const { drawerTab } = await store.get('drawerTab');
      ui.show(KO.tabs[drawerTab] ? drawerTab : 'notices');
    }
  }

  // 손잡이만 먼저 붙이고, 데이터는 처음 열 때 불러옴 (페이지마다 API를 부르지 않게)
  function mountHandle() {
    if (document.querySelector('.lmsh-ui')) return;
    ui = build();
  }
  addEventListener('keydown', e => e.key === 'Escape' && ui?.root.classList.contains('open') && toggle(false));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountHandle);
  else mountHandle();
})();
