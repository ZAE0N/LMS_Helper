"""Local learning events and generated quizzes; chat text remains on the client."""
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









@lru_cache(maxsize=8)
def _store(path: Path) -> LearningStore:
    return LearningStore(path)


def get_learning_store() -> LearningStore:
    return _store((service.DATA_DIR / 'learning.sqlite3').resolve())
