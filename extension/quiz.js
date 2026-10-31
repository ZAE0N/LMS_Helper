// 복습 문제: 한 문제씩 풀고 채점. 객관식·연결형은 여기서, 주관식은 서버가 채점
const QuizView = (() => {
  const { h } = App;
  const shuffle = arr => {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  };

  async function render(doc) {
    const { t, params } = App;
    const types = (params.get('types') || 'multiple').split(',').filter(k => t.types[k]);
    const count = Math.min(20, Math.max(1, Number(params.get('count')) || 5));
    App.status(t.makingQuiz);
    let quiz;
    try {
      quiz = await Api.quiz({ anon_id: App.anonId, doc_id: doc.doc_id, lang: App.lang, count, types, pages: null }, doc);
      if (!quiz.questions?.length) throw new Error('empty');
    } catch (e) {
      App.status(`${t.quizError} (${e.message})`, { error: true, retry: () => render(doc) });
      return;
    }
    const answers = [];
    let i = 0;
    const app = document.getElementById('app');

    function show() {
      const q = quiz.questions[i];
      const feedback = h('div', { class: 'feedback', hidden: true });
      const check = h('button', { type: 'button', class: 'btn' }, t.check);
      const nextBtn = h('button', { type: 'button', class: 'btn', hidden: true, onclick: () => (++i < quiz.questions.length ? show() : finish()) },
        i + 1 < quiz.questions.length ? t.nextQ : t.finish);
      const body = h('div', { class: 'q-body' });
      let read = () => null; // 사용자 답 → 채점 함수

      if (q.type === 'multiple') {
        let chosen = -1;
        const btns = q.choices.map((c, k) => h('button', { type: 'button', class: 'choice', onclick: () => {
          chosen = k;
          btns.forEach((b, j) => b.classList.toggle('on', j === k));
        } }, c));
        body.append(...btns);
        read = async () => {
          if (chosen < 0) return null;
          btns.forEach((b, j) => b.classList.add(j === q.answer_index ? 'right' : j === chosen ? 'bad' : 'off'));
          return { correct: chosen === q.answer_index, answerText: q.choices[q.answer_index] };
        };
      } else if (q.type === 'short') {
        const ta = h('textarea', { rows: 4, placeholder: t.yourAnswer });
        body.append(ta);
        read = async () => {
          if (!ta.value.trim()) return null;
          check.textContent = t.grading;
          const g = await Api.gradeShort({ lang: App.lang, quiz_id: quiz.quiz_id, question_id: q.id, user_answer: ta.value.trim() });
          ta.disabled = true;
          return { correct: !!g.is_correct, answerText: q.model_answer, extra: `${t.score}: ${g.score} · ${g.reason_tr || ''}` };
        };
      } else {
        const rights = shuffle(q.pairs.map(p => p[1]));
        const sels = q.pairs.map(() => h('select', {}, h('option', { value: '' }, t.choose), rights.map(r => h('option', { value: r }, r))));
        body.append(h('div', { class: 'match' }, q.pairs.map((p, k) => h('div', { class: 'match-row' }, h('span', { lang: 'ko' }, p[0]), sels[k]))));
        read = async () => {
          if (sels.some(s => !s.value)) return null;
          const ok = q.pairs.map((p, k) => sels[k].value === p[1]);
          sels.forEach((s, k) => { s.disabled = true; s.classList.add(ok[k] ? 'right' : 'bad'); });
          return { correct: ok.every(Boolean), answerText: q.pairs.map(p => `${p[0]} → ${p[1]}`).join(' · ') };
        };
      }

      check.addEventListener('click', async () => {
        check.disabled = true;
        let r;
        try {
          r = await read();
        } catch (e) {
          check.disabled = false;
          check.textContent = t.check;
          feedback.hidden = false;
          feedback.replaceChildren(h('p', { class: 'bad-text' }, `${t.quizError} (${e.message})`));
          return;
        }
        if (!r) {
          check.disabled = false;
          return;
        }
        answers.push({ id: q.id, correct: r.correct, page: q.page, type: q.type });
        check.hidden = true;
        nextBtn.hidden = false;
        feedback.hidden = false;
        feedback.className = `feedback ${r.correct ? 'ok' : 'no'}`;
        feedback.replaceChildren(...[
          h('strong', {}, r.correct ? `✓ ${t.correct}` : `✗ ${t.wrong}`),
          r.extra ? h('p', {}, r.extra) : null,
          r.correct ? null : h('p', {}, h('b', {}, `${t.answer}: `), r.answerText),
          q.explanation ? h('p', {}, h('b', {}, `${t.explanation}: `), q.explanation) : null,
          q.page ? h('a', { class: 'link', href: pageLink(q.page), target: '_blank' }, `${t.page} ${q.page} ↗`) : null].filter(Boolean));
      });

      app.replaceChildren(h('div', { class: 'quiz' },
        h('div', { class: 'q-top' }, h('span', { class: 'muted' }, t.progress(i + 1, quiz.questions.length)), h('span', { class: 'tag' }, t.types[q.type] || q.type)),
        h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill', style: `width:${(i / quiz.questions.length) * 100}%` })),
        h('h2', { class: 'q-text' }, q.question),
        body, h('div', { class: 'q-actions' }, check, nextBtn), feedback));
    }

    async function finish() {
      let res = { score: answers.filter(a => a.correct).length, total: answers.length };
      try {
        res = await Api.quizResult({ anon_id: App.anonId, quiz_id: quiz.quiz_id, answers: answers.map(({ id, correct }) => ({ id, correct })) });
      } catch {}
      const wrong = {};
      answers.filter(a => !a.correct && a.page).forEach(a => (wrong[a.page] = (wrong[a.page] || 0) + 1));
      const weak = Object.entries(wrong).sort((a, b) => b[1] - a[1]).map(([p]) => Number(p));
      await App.store.set({ lastQuiz: { score: res.score, total: res.total, weakPage: weak[0] || null, name: params.get('name'), at: Date.now() } });
      app.replaceChildren(h('div', { class: 'quiz done' },
        h('p', { class: 'muted' }, t.result),
        h('p', { class: 'big' }, `${res.score} / ${res.total}`),
        weak.length ? h('div', {}, h('h3', {}, t.wrongPages), h('div', { class: 'chips' }, weak.map(p => h('a', { class: 'chip', href: pageLink(p), target: '_blank' }, `${t.page} ${p}`)))) : null,
        h('div', { class: 'q-actions' },
          h('button', { type: 'button', class: 'btn', onclick: () => render(doc) }, t.retryQuiz),
          h('a', { class: 'btn ghost', href: pageLink(), target: '_blank' }, t.openMaterial),
          h('a', { class: 'btn ghost', href: 'viewer.html?mode=report', target: '_blank' }, t.openReport))));
    }

    // 같은 교안을 뷰어 모드로 (해당 페이지부터)
    function pageLink(page) {
      const q = new URLSearchParams(params);
      ['mode', 'types', 'count'].forEach(k => q.delete(k));
      if (page) q.set('page', page);
      return `viewer.html?${q}`;
    }

    show();
  }

  return { render };
})();
