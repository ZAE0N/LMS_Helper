// 서버에 아직 없는 API(챗봇·문제·리포트)의 가짜 응답. CONFIG.MOCK_API가 true인 기능만 사용
// 형식은 AGENTS.md 3장과 같게. 내용은 교안 페이지에서 단순하게 만든 예시 ("[demo]" 표시)
const FAKE = (() => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const store = {
    async get() { try { return (await chrome.storage.local.get('fakeData')).fakeData || { results: [], chats: 0 }; } catch { return { results: [], chats: 0 }; } },
    async set(v) { try { await chrome.storage.local.set({ fakeData: v }); } catch {} }
  };
  const words = s => (s || '').toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w => w.length >= 2);
  const excerpt = (s, n = 160) => { const t = (s || '').replace(/\s+/g, ' ').trim(); return t.length > n ? `${t.slice(0, n)}…` : t; };
  const pick = (arr, n) => [...arr].sort(() => Math.random() - 0.5).slice(0, n);
  const quizzes = new Map();

  async function chat({ question, history }, doc) {
    await wait(500);
    const q = new Set(words(question));
    const scored = doc.pages
      .map(p => ({ p, score: words(`${p.original} ${p.translated}`).filter(w => q.has(w)).length }))
      .sort((a, b) => b.score - a.score);
    const data = await store.get();
    data.chats += 1;
    await store.set(data);
    const compress = history.length > 16;
    const top = scored[0];
    if (!top || !top.score) {
      return { answer: "[demo] I couldn't find this in the course material.", citations: [], summary: compress ? '[demo] summary of earlier conversation' : null, kept: compress ? 8 : null };
    }
    return {
      answer: `[demo] Page ${top.p.page} covers this: ${excerpt(top.p.translated, 240)}`,
      citations: scored.filter(s => s.score).slice(0, 2).map(s => ({ page: s.p.page, quote: excerpt(s.p.translated, 80) })),
      summary: compress ? '[demo] summary of earlier conversation' : null,
      kept: compress ? 8 : null
    };
  }

  async function quiz({ doc_id, count, types }, doc) {
    await wait(700);
    const pages = doc.pages.filter(p => (p.translated || '').trim());
    const terms = pages.flatMap(p => (p.terms || []).map(term => ({ term, page: p.page })));
    const questions = [];
    for (let i = 0; i < count && pages.length; i++) {
      const type = types[i % types.length];
      const p = pages[i % pages.length];
      const id = `q${i + 1}`;
      if (type === 'multiple') {
        const others = pick(pages.filter(x => x.page !== p.page), 3).map(x => `Page ${x.page}`);
        const choices = pick([`Page ${p.page}`, ...others], 4);
        questions.push({ id, type, page: p.page, question: `[demo] Which page says: “${excerpt(p.translated, 90)}”?`,
          choices, answer_index: choices.indexOf(`Page ${p.page}`), explanation: `It is on page ${p.page}.` });
      } else if (type === 'short') {
        const t = terms.find(x => x.page === p.page)?.term || words(p.original)[0] || 'this page';
        questions.push({ id, type, page: p.page, question: `[demo] Explain “${t}” in your own words.`,
          model_answer: excerpt(p.translated, 200), keywords: words(p.translated).slice(0, 3), explanation: `See page ${p.page}.` });
      } else {
        const src = (terms.length >= 3 ? pick(terms, 4) : pick(pages, 3).map(x => ({ term: excerpt(x.original, 30), page: x.page })));
        questions.push({ id, type, page: src[0].page, question: '[demo] Match each item with its page.',
          pairs: src.map(x => [x.term, `Page ${x.page}`]), explanation: 'Each item appears on that page.' });
      }
    }
    const quiz_id = `demo-${Date.now()}`;
    quizzes.set(quiz_id, { doc_id, questions });
    return { quiz_id, questions };
  }

  async function gradeShort({ quiz_id, question_id, user_answer }) {
    await wait(500);
    const q = quizzes.get(quiz_id)?.questions.find(x => x.id === question_id);
    const hits = (q?.keywords || []).filter(k => (user_answer || '').toLowerCase().includes(k)).length;
    const score = Math.min(100, (user_answer || '').trim().length >= 10 ? 40 + hits * 20 : 0);
    return { is_correct: score >= 60, score, reason_tr: `[demo] ${hits} key word(s) matched.` };
  }

  async function quizResult({ quiz_id, answers }) {
    const qz = quizzes.get(quiz_id);
    const data = await store.get();
    data.results.push({ doc_id: qz?.doc_id, answers: answers.map(a => {
      const q = qz?.questions.find(x => x.id === a.id);
      return { ...a, type: q?.type, page: q?.page };
    }) });
    await store.set(data);
    return { score: answers.filter(a => a.correct).length, total: answers.length };
  }

  async function report({ doc_id }) {
    await wait(400);
    const data = await store.get();
    const results = data.results.filter(r => !doc_id || r.doc_id === doc_id);
    const all = results.flatMap(r => r.answers.map(a => ({ ...a, doc_id: r.doc_id })));
    const rate = list => (list.length ? list.filter(a => a.correct).length / list.length : 0);
    const wrong = {};
    all.filter(a => !a.correct).forEach(a => { const k = `${a.doc_id}|${a.page}`; wrong[k] = (wrong[k] || 0) + 1; });
    const weak_pages = Object.entries(wrong).sort((a, b) => b[1] - a[1]).slice(0, 5)
      .map(([k, n]) => ({ doc_id: k.split('|')[0], page: Number(k.split('|')[1]), wrong: n }));
    return {
      quizzes_taken: results.length,
      accuracy: rate(all),
      accuracy_by_type: Object.fromEntries(['multiple', 'short', 'matching'].map(t => [t, rate(all.filter(a => a.type === t))])),
      questions_asked: data.chats,
      weak_pages,
      exam_points: weak_pages.slice(0, 3).map(w => ({ point_tr: `[demo] Review page ${w.page} again`, why_tr: `You missed questions from this page ${w.wrong} time(s).`, pages: [w.page] })),
      summary_tr: results.length ? `[demo] ${results.length} quiz(zes), ${Math.round(rate(all) * 100)}% correct.` : '[demo] No review yet.'
    };
  }

  return { chat, quiz, gradeShort, quizResult, report };
})();
