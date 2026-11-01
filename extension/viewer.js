// 확장 화면 공통: 요소 만들기, 저장소, 교안 불러오기(다운로드 → 번역)
const App = (() => {
  const params = new URLSearchParams(location.search);
  const MAX_PDF = 100 * 1024 * 1024;
  const CHUNK = 1 << 20;

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

  const store = {
    get: k => chrome.storage.local.get(k).catch(() => ({})),
    set: o => chrome.storage.local.set(o).catch(() => {}),
    remove: k => chrome.storage.local.remove(k).catch(() => {})
  };

  class UserError extends Error {}
  // 비밀번호가 필요한 PDF (wrong: 틀린 비밀번호를 넣었음)
  class PasswordError extends Error {
    constructor(wrong) { super(wrong ? 'PDF_PASSWORD_WRONG' : 'PDF_PASSWORD_REQUIRED'); this.wrong = wrong; }
  }

  // 상태 표시 (불러오는 중 / 오류 + 다시 시도)
  function status(text, { retry, error } = {}) {
    const app = document.getElementById('app');
    app.replaceChildren(h('div', { class: `status${error ? ' error' : ''}` },
      error ? null : h('span', { class: 'spinner' }),
      h('p', {}, text),
      retry ? h('button', { type: 'button', class: 'btn', onclick: retry }, App.t.retry) : null));
  }

  async function lmsJson(path) {
    const r = await fetch(CONFIG.LMS_ORIGIN + path, { credentials: 'include', headers: { Accept: 'application/json' } });
    const text = await r.text();
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return JSON.parse(text.replace(/^while\(1\);/, ''));
  }

  // 뷰어가 직접 못 받으면 뷰어를 연 LMS 탭(content.js)에 부탁
  function viaTab(msg) {
    const tab = Number(params.get('tab'));
    if (!tab) return Promise.reject(new Error('no LMS tab'));
    return chrome.tabs.sendMessage(tab, msg).then(r => {
      if (!r?.ok) throw new Error(r?.error || 'LMS tab');
      return r.data;
    });
  }

  async function fileMeta(course, file) {
    try {
      const f = await lmsJson(`/api/v1/courses/${course}/files/${file}`);
      return { name: f.display_name, size: f.size, updated_at: f.updated_at || f.created_at || '', url: f.url };
    } catch {
      return viaTab({ type: 'file-meta', course, file });
    }
  }

  async function download(course, file, meta) {
    if (meta.url) {
      try {
        const r = await fetch(meta.url, { credentials: 'include' });
        if (r.ok) return await r.blob();
      } catch {}
    }
    const parts = [];
    let offset = 0;
    let total = 1;
    while (offset < total) {
      const res = await viaTab({ type: 'read-file', course, file, offset, size: CHUNK });
      total = res.total;
      const bin = atob(res.data);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      if (!bytes.length) break;
      parts.push(bytes);
      offset += bytes.length;
    }
    return new Blob(parts, { type: 'application/pdf' });
  }

  // 같은 파일·같은 수정 시각·같은 언어면 서버에 저장된 번역을 다시 씀
  // force: 저장된 번역이 있어도 파일을 다시 보냄 (서버에 원본이 없을 때, 예: PDF 보기)
  async function loadDoc(onStep, { force = false, password = null } = {}) {
    const { t, lang } = App;
    const course = params.get('course');
    const file = params.get('file');
    const key = `doc:v8:${file}:${lang}`; // 교재 보기 형식이 바뀌면 올려서 다시 번역
    onStep(t.step.meta);
    const meta = await fileMeta(course, file).catch(() => { throw new UserError(t.errDownload); });
    const saved = (await store.get(key))[key];
    if (!force && saved && saved.updated_at === meta.updated_at) {
      onStep(t.step.cached);
      try { return await Api.getDocument(saved.doc_id); } catch {}
    }
    if (meta.size > MAX_PDF) throw new UserError(t.errTooBig);
    onStep(t.step.download);
    const blob = await download(course, file, meta).catch(() => { throw new UserError(t.errDownload); });
    const head = new TextDecoder().decode(await blob.slice(0, 5).arrayBuffer());
    if (head !== '%PDF-') throw new UserError(t.errNotPdf);
    onStep(t.step.translate);
    const name = meta.name || params.get('name') || 'document.pdf';
    const glossary = CONFIG.GLOSSARY[params.get('courseName')] || null;
    const doc = await Api.translateDocument(blob, name, lang, glossary, password).catch(e => {
      if (e.status === 401 && /^PDF_PASSWORD_(REQUIRED|WRONG)$/.test(e.detail)) throw new PasswordError(e.detail.endsWith('WRONG'));
      if (/encrypted/i.test(e.message)) throw new UserError(t.errPasswordUnsupported); // 서버가 아직 비밀번호를 지원하지 않을 때
      throw new UserError(e instanceof TypeError ? t.errServer : `${t.errServerFail} (${e.message})`); // TypeError = 연결 자체 실패
    });
    await store.set({
      [key]: { doc_id: doc.doc_id, updated_at: meta.updated_at },
      [`docmeta:${doc.doc_id}`]: { name: params.get('name') || name, courseName: params.get('courseName'), file, course }
    });
    return doc;
  }

  // 비밀번호 입력 (입력값은 이 요청에만 쓰고 저장하지 않음)
  function askPassword(wrong, onSubmit) {
    const { t } = App;
    const input = h('input', { type: 'password', class: 'pw', autocomplete: 'off', 'aria-label': t.password, placeholder: t.password });
    const submit = () => input.value && onSubmit(input.value);
    input.addEventListener('keydown', e => e.key === 'Enter' && submit());
    document.getElementById('app').replaceChildren(h('div', { class: 'status' },
      h('p', { class: wrong ? 'bad-text' : '' }, wrong ? t.errPasswordWrong : t.errPassword),
      h('div', { class: 'pw-row' }, input, h('button', { type: 'button', class: 'btn', onclick: submit }, t.open))));
    input.focus();
  }

  return { params, h, store, status, loadDoc, askPassword, UserError, PasswordError, lang: 'en', t: null, anonId: null };
})();
