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







@lru_cache(maxsize=8)
def _store(path: Path) -> LearningStore:
    return LearningStore(path)


def get_learning_store() -> LearningStore:
    return _store((service.DATA_DIR / 'learning.sqlite3').resolve())
