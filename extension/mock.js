// CONFIG.MOCK_API.notices 일 때만 사용. AGENTS.md 3장 Notice 형식과 같아야 함 (한국어, 번역은 크롬이 함)
const MOCK = (() => {
  const day = 86400000;
  const iso = offset => new Date(Date.now() + offset * day).toISOString();
  const date = offset => iso(offset).slice(0, 10);

  const T = {
    school: [
      ['2026년 2학기 선원가족장학사업 장학생 모집 접수기간 연장 안내', '접수기간이 연장되었습니다.', 5, '학생지원팀에 신청서 제출'],
      ['[보건실] 2026학년도 2학기 결핵 예방을 위한 건강검진 시행 안내', '전체 재학생 대상 건강검진을 실시합니다.', 2, '검진 기간에 보건실 방문'],
      ['천원의 아침밥 무료제공 이벤트 안내', '이벤트 기간 동안 아침밥을 무료로 제공합니다.', null, null],
      ['2026학년도 동계 계절학기 수강신청 안내', '동계 계절학기 수강신청 일정을 안내합니다.', 12, '포털에서 수강신청'],
      ['도서관 시험기간 연장 개관 안내', '시험기간 동안 열람실을 연장 운영합니다.', null, null]
    ],
    dept: [
      ['[컴퓨터정보공학과] 제42대 학회장·부학회장 선출 안내', '학회장·부학회장 선거를 실시합니다.', 10, '선거 기간에 투표'],
      ['2026년 SK인천석유화학 기술직 인턴 채용상담회 개최 안내', '기술직 인턴 채용상담회를 개최합니다.', 1, '사전 신청서 제출'],
      ['2026학년도 2학기 결핵예방을 위한 흉부 X-RAY 검사(재학생)', '재학생 흉부 X-RAY 검사를 실시합니다.', -2, '지정일에 검사'],
      ['캡스톤디자인 작품 전시회 참가 안내', '학과 캡스톤디자인 작품 전시회 참가팀을 모집합니다.', 8, '팀별 참가 신청']
    ]
  };

  const build = source =>
    T[source].map(([title, summary, due, action], i) => ({
      id: `${source}-mock-${i}`,
      source,
      course: null,
      title,
      summary,
      body: `${summary}${action ? `\n해야 할 일: ${action}` : ''}`,
      deadline: due === null ? null : date(due),
      deadline_text: due === null ? null : date(due),
      action,
      posted_at: iso(-(i + 1)),
      url: CONFIG.LMS_ORIGIN
    }));

  // 날짜 표현을 찾아 마감일로 (서버 추출을 흉내냄)
  const DATE_RE = /(\d{1,2})\s*[/.월]\s*(\d{1,2})\s*일?/;
  const ACTION_RES = [/[^.\n]*(제출|신청|등록|지참)[^.\n]*/, /[^.\n]*(확인|준비)[^.\n]*/]; // 앞의 것이 우선

  return {
    notices({ source, limit, page }) {
      const all = build(source);
      const start = (page - 1) * limit;
      return { items: all.slice(start, start + limit), page, has_more: start + limit < all.length };
    },
    extract({ items }) {
      return {
        items: items.map(it => {
          const text = `${it.title}\n${it.body}`;
          const m = text.match(DATE_RE);
          const y = new Date().getFullYear();
          const deadline = m ? `${y}-${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}` : null;
          const a = ACTION_RES.map(re => (it.body || '').match(re) || it.title.match(re)).find(Boolean); // 본문 우선
          return {
            id: it.id,
            summary: it.body.split('\n')[0].slice(0, 80),
            deadline,
            deadline_text: m ? m[0] : null,
            action: a ? a[0].trim() : null
          };
        })
      };
    }
  };
})();
