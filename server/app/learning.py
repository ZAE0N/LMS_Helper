"""Local learning events and generated quizzes; chat text remains on the client."""
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from collections.abc import Iterator

from . import service


class LearningStoreError(Exception):
    pass


class QuizNotFound(Exception):
    pass


class QuizForbidden(Exception):
    pass


class QuizConflict(Exception):
    pass


class QuizSubmissionError(Exception):
    pass


class LearningStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as connection:
                connection.executescript('''
                    CREATE TABLE IF NOT EXISTS question_events (
                        id INTEGER PRIMARY KEY,
                        anon_id TEXT NOT NULL,
                        doc_id TEXT NOT NULL,
                        lang TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS question_events_anon_doc
                        ON question_events (anon_id, doc_id);
                    CREATE TABLE IF NOT EXISTS quizzes (
                        quiz_id TEXT PRIMARY KEY,
                        anon_id TEXT NOT NULL,
                        doc_id TEXT NOT NULL,
                        lang TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        questions_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS quizzes_anon_doc ON quizzes (anon_id, doc_id);
                    CREATE TABLE IF NOT EXISTS short_grades (
                        quiz_id TEXT NOT NULL REFERENCES quizzes(quiz_id),
                        question_id TEXT NOT NULL,
                        answer_hash TEXT NOT NULL,
                        lang TEXT NOT NULL,
                        score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
                        is_correct INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
                        reason_tr TEXT NOT NULL,
                        graded_at TEXT NOT NULL,
                        PRIMARY KEY (quiz_id, question_id)
                    );
                    CREATE TABLE IF NOT EXISTS quiz_results (
                        quiz_id TEXT PRIMARY KEY REFERENCES quizzes(quiz_id),
                        score INTEGER NOT NULL CHECK (score >= 0),
                        total INTEGER NOT NULL CHECK (total > 0 AND score <= total),
                        submitted_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS quiz_answer_results (
                        quiz_id TEXT NOT NULL REFERENCES quiz_results(quiz_id),
                        question_id TEXT NOT NULL,
                        type TEXT NOT NULL CHECK (type IN ('multiple', 'short', 'matching')),
                        page INTEGER NOT NULL CHECK (page > 0),
                        correct INTEGER NOT NULL CHECK (correct IN (0, 1)),
                        PRIMARY KEY (quiz_id, question_id)
                    );
                ''')
        except OSError as e:
            raise LearningStoreError('학습 기록 저장소를 열 수 없음') from e

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                connection.execute('PRAGMA foreign_keys = ON')
                with connection:
                    yield connection
        except sqlite3.Error as e:
            raise LearningStoreError('학습 기록 저장소 오류') from e

    def record_question(self, anon_id: str, doc_id: str, lang: str) -> None:
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO question_events (anon_id, doc_id, lang, created_at) VALUES (?, ?, ?, ?)',
                (anon_id, doc_id, lang, datetime.now(timezone.utc).isoformat()),
            )

    def question_count(self, anon_id: str, doc_id: str | None = None) -> int:
        with self._connection() as connection:
            if doc_id is None:
                row = connection.execute('SELECT COUNT(*) FROM question_events WHERE anon_id = ?', (anon_id,)).fetchone()
            else:
                row = connection.execute('SELECT COUNT(*) FROM question_events WHERE anon_id = ? AND doc_id = ?', (anon_id, doc_id)).fetchone()
            return row[0]

    def save_quiz(self, quiz_id: str, anon_id: str, doc_id: str, lang: str, questions: list[dict]) -> None:
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO quizzes (quiz_id, anon_id, doc_id, lang, created_at, questions_json) VALUES (?, ?, ?, ?, ?, ?)',
                (quiz_id, anon_id, doc_id, lang, datetime.now(timezone.utc).isoformat(), json.dumps(questions, ensure_ascii=False)),
            )

    def get_quiz(self, quiz_id: str) -> dict | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT * FROM quizzes WHERE quiz_id = ?', (quiz_id,)).fetchone()
            if row is None:
                return None
            record = dict(row)
            record['questions'] = json.loads(record.pop('questions_json'))
            return record

    def get_short_grade(self, quiz_id: str, question_id: str) -> dict | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT * FROM short_grades WHERE quiz_id = ? AND question_id = ?',
                                     (quiz_id, question_id)).fetchone()
            return dict(row) if row is not None else None

    def get_quiz_result(self, quiz_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute('SELECT score, total FROM quiz_results WHERE quiz_id = ?', (quiz_id,)).fetchone()
            return {'score': row[0], 'total': row[1]} if row is not None else None

    def save_short_grade(self, quiz_id: str, question_id: str, answer_hash: str, lang: str,
                         score: int, is_correct: bool, reason_tr: str) -> dict:
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM quiz_results WHERE quiz_id = ?', (quiz_id,)).fetchone():
                raise QuizConflict('이미 제출한 시험의 답변은 변경할 수 없음')
            connection.execute('''INSERT INTO short_grades
                (quiz_id, question_id, answer_hash, lang, score, is_correct, reason_tr, graded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (quiz_id, question_id) DO UPDATE SET answer_hash=excluded.answer_hash,
                lang=excluded.lang, score=excluded.score, is_correct=excluded.is_correct,
                reason_tr=excluded.reason_tr, graded_at=excluded.graded_at''',
                (quiz_id, question_id, answer_hash, lang, score, int(is_correct), reason_tr,
                 datetime.now(timezone.utc).isoformat()))
        return {'score': score, 'is_correct': is_correct, 'reason_tr': reason_tr}

    def submit_quiz_result(self, quiz_id: str, anon_id: str, answers: dict[str, bool]) -> dict:
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.row_factory = sqlite3.Row
            quiz = connection.execute('SELECT * FROM quizzes WHERE quiz_id = ?', (quiz_id,)).fetchone()
            if quiz is None:
                raise QuizNotFound('복습 문제 없음')
            if quiz['anon_id'] != anon_id:
                raise QuizForbidden('다른 사용자의 복습 문제')
            questions = json.loads(quiz['questions_json'])
            if set(answers) != {q['id'] for q in questions}:
                raise QuizSubmissionError('모든 문제에 대해 한 번씩 결과를 보내야 함')
            grades = {row['question_id']: bool(row['is_correct']) for row in connection.execute(
                'SELECT question_id, is_correct FROM short_grades WHERE quiz_id = ?', (quiz_id,))}
            checked = {}
            for question in questions:
                qid = question['id']
                if question['type'] == 'short':
                    if qid not in grades:
                        raise QuizSubmissionError('주관식 답변을 먼저 채점해야 함')
                    checked[qid] = grades[qid]
                else:
                    checked[qid] = answers[qid]
            previous = connection.execute('SELECT score, total FROM quiz_results WHERE quiz_id = ?', (quiz_id,)).fetchone()
            if previous is not None:
                stored = {row['question_id']: bool(row['correct']) for row in connection.execute(
                    'SELECT question_id, correct FROM quiz_answer_results WHERE quiz_id = ?', (quiz_id,))}
                if stored != checked:
                    raise QuizConflict('이미 제출한 결과와 다름. 새 복습 문제를 생성하세요.')
                return {'score': previous['score'], 'total': previous['total']}
            score, total = sum(checked.values()), len(questions)
            connection.execute('INSERT INTO quiz_results (quiz_id, score, total, submitted_at) VALUES (?, ?, ?, ?)',
                               (quiz_id, score, total, datetime.now(timezone.utc).isoformat()))
            connection.executemany('INSERT INTO quiz_answer_results (quiz_id, question_id, type, page, correct) VALUES (?, ?, ?, ?, ?)',
                                   [(quiz_id, q['id'], q['type'], q['page'], int(checked[q['id']])) for q in questions])
            return {'score': score, 'total': total}

    def report_statistics(self, anon_id: str, doc_id: str | None = None) -> dict:
        conditions = 'q.anon_id = ?' + (' AND q.doc_id = ?' if doc_id else '')
        params = (anon_id, doc_id) if doc_id else (anon_id,)
        event_conditions = 'anon_id = ?' + (' AND doc_id = ?' if doc_id else '')
        with self._connection() as connection:
            connection.execute('BEGIN')
            taken, correct, total = connection.execute(f'''SELECT COUNT(*), COALESCE(SUM(r.score), 0), COALESCE(SUM(r.total), 0)
                FROM quiz_results r JOIN quizzes q ON q.quiz_id = r.quiz_id WHERE {conditions}''', params).fetchone()
            asked = connection.execute(f'SELECT COUNT(*) FROM question_events WHERE {event_conditions}', params).fetchone()[0]
            accuracy_by_type = {kind: 0.0 for kind in ('multiple', 'short', 'matching')}
            answered_by_type = {kind: 0 for kind in accuracy_by_type}
            for kind, n, right in connection.execute(f'''SELECT a.type, COUNT(*), SUM(a.correct)
                    FROM quiz_answer_results a JOIN quizzes q ON q.quiz_id = a.quiz_id
                    WHERE {conditions} GROUP BY a.type''', params):
                accuracy_by_type[kind] = right / n
                answered_by_type[kind] = n
            weak = [{'doc_id': doc, 'page': page, 'wrong': wrong} for doc, page, wrong in connection.execute(f'''
                SELECT q.doc_id, a.page, COUNT(*) FROM quiz_answer_results a JOIN quizzes q ON q.quiz_id = a.quiz_id
                WHERE {conditions} AND a.correct = 0 GROUP BY q.doc_id, a.page
                ORDER BY COUNT(*) DESC, q.doc_id, a.page LIMIT 20''', params)]
            documents = sorted({row[0] for row in connection.execute(
                f'SELECT DISTINCT doc_id FROM question_events WHERE {event_conditions}', params)} |
                {row[0] for row in connection.execute(f'SELECT DISTINCT q.doc_id FROM quizzes q WHERE {conditions}', params)})
        return {'quizzes_taken': taken, 'accuracy': correct / total if total else 0.0,
                'accuracy_by_type': accuracy_by_type, 'questions_asked': asked, 'weak_pages': weak,
                'document_ids': documents, 'answered_by_type': answered_by_type}


@lru_cache(maxsize=8)
def _store(path: Path) -> LearningStore:
    return LearningStore(path)


def get_learning_store() -> LearningStore:
    return _store((service.DATA_DIR / 'learning.sqlite3').resolve())
