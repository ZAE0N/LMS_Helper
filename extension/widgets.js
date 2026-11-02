// LMS 모든 페이지 오른쪽 끝의 서랍 패널 (공지·교안·복습). 문구는 한국어, 번역은 크롬 번역이 함
(() => {
  if (window !== top || QUIZ_PATH.test(location.pathname)) return;

  const KO = {
    brand: 'LMS Helper',
    tabs: { notices: '공지', materials: '교안', review: '복습' },
    allCourses: '전체 과목',
    allCategories: '전체 분류',
    pager: '쪽 이동',
    prev: '이전 쪽',
    next: '다음 쪽',
    original: '원본',
    translatedPdf: '번역본',
    close: '닫기',
    sources: { course: '수업', school: '학교', dept: '학과' },
    closed: '마감됨',
    today: '오늘 마감',
    empty: '공지가 없습니다',
    loading: '불러오는 중…',
    error: '불러오지 못했습니다',
    sample: '예시 데이터',
    open: '열기',
    review: '복습',
    noFiles: '올라온 파일이 없습니다',
    noCourses: '수강 중인 과목이 없습니다',
    filesCount: n => `파일 ${n}개`,
    reviewFor: '복습할 교안',
    pages: '쪽 범위 (비우면 전체)',
    from: '시작',
    to: '끝',
    download: '내려받기',
    view: '교안 보기',
    langLabel: '교안 번역 언어',
    reviewLang: '문제·리포트 언어',
    changeInMaterials: '교안 탭에서 변경',
    kinds: { pdf: '교안 (PDF)', slides: '발표 자료', docs: '문서', code: '코드·실습 파일', archive: '압축 파일',
             image: '그림', media: '동영상·소리', etc: '기타' },
    types: { multiple: '객관식', short: '주관식', matching: '연결형' },
    count: n => `${n}문제`,
    start: '복습 시작',
    lastResult: '최근 결과',
    weak: '약한 부분',
    noRecord: '아직 복습 기록이 없습니다',
    report: '학습 리포트 보기'
  };
  const FILE_CACHE_MS = 10 * 60 * 1000;
  // 서랍 문구는 한국어(크롬 번역)지만 언어 이름은 그 언어로 그대로 보여 줌
  const LANG_NAMES = { ko: '한국어', en: 'English', ru: 'Русский' };
  const KIND_EXT = {
    pdf: ['pdf'],
    slides: ['ppt', 'pptx', 'key', 'odp'],
    docs: ['hwp', 'hwpx', 'doc', 'docx', 'txt', 'md', 'rtf', 'xls', 'xlsx', 'csv', 'odt'],
    code: ['js', 'ts', 'py', 'java', 'c', 'cpp', 'h', 'cs', 'html', 'css', 'json', 'sol', 'ipynb', 'sql', 'sh', 'xml', 'yml', 'yaml'],
    archive: ['zip', '7z', 'rar', 'tar', 'gz', 'egg', 'alz'],
    image: ['png', 'jpg', 'jpeg', 'gif', 'svg', 'bmp', 'webp'],
    media: ['mp4', 'mov', 'avi', 'mkv', 'webm', 'mp3', 'wav', 'm4a']
  };
  const kindOf = name => {
    const ext = (name.match(/\.([a-z0-9]+)$/i)?.[1] || '').toLowerCase();
    return Object.keys(KIND_EXT).find(k => KIND_EXT[k].includes(ext)) || 'etc';
  };
  const extOf = name => (name.match(/\.([a-z0-9]+)$/i)?.[1] || 'FILE').toUpperCase().slice(0, 4);
  // LMS의 다운로드 주소를 그대로 엶 (로그인 세션으로 브라우저가 직접 받음, 서버를 거치지 않음)
  const downloadUrl = f => `/courses/${f.courseId}/files/${f.id}/download?download_frd=1`;

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
    getNotices: q => (CONFIG.MOCK_API.notices ? Promise.resolve(MOCK.notices(q)) : bg({ type: 'api', path: `/notices?${new URLSearchParams(q)}` })),
    extract: items =>
      CONFIG.MOCK_API.notices ? Promise.resolve(MOCK.extract({ items })) : bg({ type: 'api', method: 'POST', path: '/notices/extract', body: { items } })
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

  // 파일 탭 → 막혀 있으면 모듈 경유. 모든 종류 (PDF는 교안 보기, 나머지는 내려받기)
  async function getCourseFiles(course) {
    const key = `lmsh:files2:${course.id}`;
    try {
      const c = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (c && Date.now() - c.at < FILE_CACHE_MS) return c.items;
    } catch {}
    let items = [];
    try {
      const q = new URLSearchParams({ sort: 'created_at', order: 'desc', per_page: '100' });
      items = (await canvasGet(`/api/v1/courses/${course.id}/files?${q}`)).map(f => ({ id: f.id, name: f.display_name, created_at: f.created_at }));
    } catch {
      try {
        const mods = await canvasGet(`/api/v1/courses/${course.id}/modules?include[]=items&per_page=50`);
        items = mods.flatMap(m => m.items || [])
          .filter(i => i.type === 'File')
          .map(i => ({ id: i.content_id, name: i.title, created_at: null }));
      } catch {}
    }
    items = items.map(f => ({ ...f, courseId: course.id, course: course.name, kind: kindOf(f.name || '') }));
    try { sessionStorage.setItem(key, JSON.stringify({ at: Date.now(), items })); } catch {}
    return items;
  }

  const viewerUrl = (f, extra = {}) =>
    `viewer.html?${new URLSearchParams({ file: f.id, course: f.courseId, name: f.name, courseName: f.course, ...extra })}`;

  // ---------- 탭: 공지 ----------
  // 학교·학과 홈페이지처럼 한 쪽에 10개, 쪽 번호로 이동. 누르면 새 탭에서 원문
  const PAGE_SIZE = 10;
  function noticesTab() {
    const state = { source: 'course', course: null, category: null, page: 1, known: 1, total: null, seq: 0 };
    const list = h('div', { class: 'lmsh-list' });
    const status = h('p', { class: 'lmsh-status' });
    const pager = h('nav', { class: 'lmsh-pager', 'aria-label': KO.pager });
    const filters = h('span', { class: 'lmsh-filters' });
    const seg = Object.entries(KO.sources).map(([key, label]) =>
      h('button', { type: 'button', 'data-s': key, onclick: () => {
        Object.assign(state, { source: key, course: null, category: null });
        load(1, true);
      } }, label));

    const row = n => {
      const d = dday(n.deadline);
      const link = n.url?.startsWith('https://');
      return h(link ? 'a' : 'div', { class: 'lmsh-notice', ...(link ? { href: n.url, target: '_blank', rel: 'noopener noreferrer' } : {}) },
        h('span', { class: `lmsh-dday ${d ? d.level : 'none'}` }, d ? d.text : ''),
        h('strong', { class: 'lmsh-title' }, n.title),
        h('span', { class: 'lmsh-side' },
          n.course && !state.course ? h('span', { class: 'lmsh-course' }, n.course) : null,
          n.category ? h('span', { class: 'lmsh-course' }, n.category) : null,
          n.posted_at ? h('span', { class: 'lmsh-date' }, fmtDate(n.posted_at)) : null));
    };

    // 세부 분류는 드롭다운: 수업 = 과목, 학교·학과 = 분류 (서버가 categories를 주면)
    function dropdown(allLabel, options, current, pick) {
      const sel = h('select', { class: 'lmsh-select-sm lmsh-filter', onchange: e => pick(e.target.value || null) },
        [['', allLabel], ...options].map(([value, label]) => h('option', { value }, label)));
      sel.value = current ?? '';
      return sel;
    }
    async function paintFilters(res) {
      if (state.source === 'course') {
        const cs = await getCourses().catch(() => []);
        filters.replaceChildren(dropdown(KO.allCourses, cs.map(c => [String(c.id), c.name]), state.course,
          v => { state.course = v; load(1, true); }));
      } else if (res?.categories?.length) {
        filters.replaceChildren(dropdown(KO.allCategories, res.categories.map(c => [c, c]), state.category,
          v => { state.category = v; load(1, true); }));
      } else filters.replaceChildren();
    }

    function paintPager() {
      const last = state.total || state.known;
      const from = Math.max(1, Math.min(state.page - 3, last - 6));
      const nums = [];
      for (let i = from; i <= Math.min(last, from + 6); i++) nums.push(i);
      const btn = (label, page, extra = {}) => h('button', { type: 'button', onclick: () => load(page), ...extra }, label);
      pager.replaceChildren(
        btn('‹', state.page - 1, { disabled: state.page <= 1, 'aria-label': KO.prev }),
        ...nums.map(i => btn(String(i), i, { class: i === state.page ? 'on' : '', 'aria-current': i === state.page ? 'page' : null })),
        btn('›', state.page + 1, { disabled: state.page >= last, 'aria-label': KO.next }));
      pager.hidden = last <= 1;
    }

    async function courseNotices(page) {
      const raw = await getAnnouncements({ limit: PAGE_SIZE, page, courseId: state.course });
      const key = it => `ex:${it.id}:${it.updated_at}`;
      const cached = await store.get(raw.items.map(key));
      const todo = raw.items.filter(it => !cached[key(it)]);
      if (todo.length) {
        // 마감일(D-day) 추출. 실패(서버 꺼짐)해도 공지는 그대로 보여줌
        const items = await Api.extract(todo.map(({ id, title, body, posted_at }) => ({ id, title, body, posted_at })))
          .then(r => r.items || [], () => []);
        const fresh = {};
        items.forEach(x => {
          const src = todo.find(t => t.id === x.id);
          if (!src) return;
          cached[key(src)] = x;
          if (x.summary) fresh[key(src)] = x; // 추출 실패(null)는 저장 안 함 → 다음에 다시 시도
        });
        if (!CONFIG.MOCK_API.notices) store.set(fresh);
      }
      return {
        items: raw.items.map(it => ({ ...it, deadline: null, ...(cached[key(it)] || {}), id: it.id })),
        has_more: raw.has_more
      };
    }

    async function load(page, reset = false) {
      const seq = ++state.seq;
      if (reset) Object.assign(state, { known: 1, total: null });
      state.page = Math.max(1, page);
      seg.forEach(b => b.classList.toggle('on', b.dataset.s === state.source));
      if (reset) paintFilters();
      status.textContent = KO.loading;
      list.replaceChildren();
      pager.hidden = true;
      try {
        const q = { source: state.source, limit: PAGE_SIZE, page: state.page, ...(state.category ? { category: state.category } : {}) };
        const res = state.source === 'course' ? await courseNotices(state.page) : await Api.getNotices(q);
        if (seq !== state.seq) return;
        const items = dedupe(res.items);
        list.replaceChildren(...items.map(row));
        status.textContent = items.length ? '' : KO.empty;
        state.total = res.total_pages || null; // 서버가 전체 쪽수를 주면 사용, 없으면 지금까지 본 쪽 + 다음 쪽
        state.known = Math.max(state.known, state.page + (res.has_more ? 1 : 0));
        if (reset && state.source !== 'course') paintFilters(res);
        paintPager();
        list.parentElement?.scrollTo?.(0, 0);
      } catch (e) {
        if (seq === state.seq) status.textContent = `${KO.error} (${e.message})`;
      }
    }

    const el = h('div', { class: 'lmsh-tab' },
      h('div', { class: 'lmsh-toolbar' }, h('div', { class: 'lmsh-seg' }, seg), filters,
        CONFIG.MOCK_API.notices ? h('span', { class: 'lmsh-sample' }, KO.sample) : null),
      list, status, pager);
    return { el, start: () => load(1, true) };
  }

  // ---------- 탭: 교안 (과목별 → 종류별) ----------
  function materialsTab(onReview) {
    const list = h('div', { class: 'lmsh-courses' }, h('p', { class: 'lmsh-status' }, KO.loading));

    const fileRow = f => h('div', { class: 'lmsh-file' },
      h('span', { class: `lmsh-pdf kind-${f.kind}` }, extOf(f.name)),
      h('div', { class: 'lmsh-main' },
        h('strong', { class: 'lmsh-title' }, f.name),
        f.created_at ? h('span', { class: 'lmsh-sub' }, fmtDate(f.created_at)) : null),
      f.kind === 'pdf' ? h('button', { type: 'button', class: 'lmsh-btn ghost', onclick: () => onReview(f) }, KO.review) : null,
      h('a', { class: 'lmsh-btn ghost lmsh-dl', href: downloadUrl(f), download: '', title: KO.download },
        f.kind === 'pdf' ? `${KO.original} ⬇` : '⬇'),
      // 번역본: 교안 보기를 열어 번역(처음이면 변환) 후 인쇄 → PDF로 저장. 한국어일 때는 숨김
      f.kind === 'pdf' ? h('button', { type: 'button', class: 'lmsh-btn ghost lmsh-dl-tr',
        onclick: () => openPage(viewerUrl(f, { save: 1 })) }, `${KO.translatedPdf} ⬇`) : null,
      f.kind === 'pdf' ? h('button', { type: 'button', class: 'lmsh-btn', onclick: () => openPage(viewerUrl(f)) }, KO.view) : null);

    const kindGroup = (kind, files) => h('details', { class: 'lmsh-kind' },
      h('summary', {}, KO.kinds[kind], h('span', { class: 'lmsh-count' }, String(files.length))),
      files.map(fileRow));

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
        const groups = Object.keys(KO.kinds).map(k => [k, files.filter(f => f.kind === k)]).filter(([, fs]) => fs.length);
        body.replaceChildren(...(groups.length ? groups.map(([k, fs]) => kindGroup(k, fs)) : [h('p', { class: 'lmsh-status' }, KO.noFiles)]));
      } },
        h('summary', {}, h('strong', {}, course.name), count),
        body);
      return box;
    }

    const start = () =>
      getCourses()
        .then(cs => list.replaceChildren(...(cs.length ? cs.map(courseBlock) : [h('p', { class: 'lmsh-status' }, KO.noCourses)])))
        .catch(e => list.replaceChildren(h('p', { class: 'lmsh-status' }, `${KO.error} (${e.message})`)));
    return { el: h('div', { class: 'lmsh-tab' }, prefs(), list), start };
  }

  // ---------- 탭: 복습 ----------
  function reviewTab() {
    const picked = { multiple: true, short: true, matching: false };
    let files = [];
    const select = h('select', { class: 'lmsh-select' }, h('option', {}, KO.loading));
    const count = h('select', { class: 'lmsh-select-sm' }, [5, 10, 15, 20].map(n => h('option', { value: n }, KO.count(n))));
    const num = label => h('input', { type: 'number', min: 1, class: 'lmsh-num', placeholder: label, 'aria-label': label });
    const from = num(KO.from);
    const to = num(KO.to);
    const chips = Object.entries(KO.types).map(([k, label]) =>
      h('button', { type: 'button', class: `lmsh-chip${picked[k] ? ' on' : ''}`, onclick: e => {
        picked[k] = !picked[k];
        if (!Object.values(picked).some(Boolean)) picked[k] = true;
        e.currentTarget.classList.toggle('on', picked[k]);
      } }, label));
    const go = h('button', { type: 'button', class: 'lmsh-btn wide', disabled: true, onclick: () => {
      const f = files.find(x => String(x.id) === select.value);
      if (!f) return;
      const a = Number(from.value) || null;
      const b = Number(to.value) || null;
      const pages = a || b ? `${a || 1}-${b || a}` : null; // 끝을 비우면 시작 쪽만
      openPage(viewerUrl(f, { mode: 'review', types: Object.keys(picked).filter(k => picked[k]).join(','), count: count.value,
        ...(pages ? { pages } : {}) }));
    } }, KO.start);
    const record = h('p', { class: 'lmsh-sub' }, KO.noRecord);
    let wanted = null;

    async function start() {
      const cs = await getCourses().catch(() => []);
      const groups = await Promise.all(cs.map(async c => ({ c, files: await getCourseFiles(c).catch(() => []) })));
      groups.forEach(g => (g.files = g.files.filter(f => f.kind === 'pdf')));
      files = groups.flatMap(g => g.files);
      const opts = groups.filter(g => g.files.length).map(g =>
        h('optgroup', { label: g.c.name }, g.files.map(f => h('option', { value: f.id }, f.name))));
      select.replaceChildren(...(opts.length ? opts : [h('option', {}, KO.noFiles)]));
      go.disabled = !files.length;
      if (wanted) select.value = String(wanted.id);
      const { lastQuiz } = await store.get('lastQuiz');
      if (lastQuiz) record.textContent = `${KO.lastResult} ${lastQuiz.score}/${lastQuiz.total}${lastQuiz.weakPage ? ` · ${KO.weak}: ${lastQuiz.weakPage}쪽` : ''}`;
    }

    const langName = h('span', { translate: 'no' });
    const paintLang = lang => (langName.textContent = LANG_NAMES[CONFIG.LANGS.includes(lang) ? lang : 'en']);
    store.get('uiLang').then(({ uiLang }) => paintLang(uiLang));
    chrome.storage.onChanged.addListener((c, area) => area === 'local' && c.uiLang && paintLang(c.uiLang.newValue));
    const el = h('div', { class: 'lmsh-tab' },
      h('p', { class: 'lmsh-sub lmsh-lang-note' }, `${KO.reviewLang}: `, langName, ` (${KO.changeInMaterials})`),
      h('label', { class: 'lmsh-label' }, KO.reviewFor), select,
      h('div', { class: 'lmsh-chips' }, chips, count),
      h('label', { class: 'lmsh-label' }, KO.pages), h('div', { class: 'lmsh-range' }, from, '~', to),
      go, record,
      h('button', { type: 'button', class: 'lmsh-btn ghost wide', onclick: () => openPage('viewer.html?mode=report') }, KO.report));
    return { el, start, preselect: f => { wanted = f; if (files.length) select.value = String(f.id); } };
  }

  // ---------- 교안 번역 언어 (교안 탭 맨 위. 챗봇·복습·리포트도 같은 언어) ----------
  function prefs() {
    const btns = CONFIG.LANGS.map(code => h('button', { type: 'button', 'data-l': code, lang: code,
      onclick: () => store.set({ uiLang: code, uiLangManual: true }) }, LANG_NAMES[code]));
    const paint = lang => btns.forEach(b => b.classList.toggle('on', b.dataset.l === lang));
    store.get('uiLang').then(({ uiLang }) => paint(CONFIG.LANGS.includes(uiLang) ? uiLang : 'en'));
    chrome.storage.onChanged.addListener((c, area) => area === 'local' && c.uiLang && paint(c.uiLang.newValue));
    return h('div', { class: 'lmsh-prefs' },
      h('div', { class: 'lmsh-prefs-row' },
        h('span', { class: 'lmsh-sub' }, KO.langLabel),
        h('div', { class: 'lmsh-seg lmsh-langs', translate: 'no' }, btns)));
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
    const langClass = lang => root.classList.toggle('lang-ko', lang === 'ko');
    store.get('uiLang').then(({ uiLang }) => langClass(uiLang));
    chrome.storage.onChanged.addListener((c, area) => area === 'local' && c.uiLang && langClass(c.uiLang.newValue));
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
  // 확장 아이콘을 누르면 서랍을 열고 닫음 (background.js)
  chrome.runtime.onMessage.addListener(msg => { if (msg.type === 'toggle-drawer') toggle(); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountHandle);
  else mountHandle();
})();
