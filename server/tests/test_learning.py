import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.chat import ChatResponse
from app.learning import LearningStore, LearningStoreError, get_learning_store


@pytest.fixture
def store(tmp_path):
    return LearningStore(tmp_path / 'data' / 'learning.sqlite3')


def test_question_counts_separate_students_and_documents(store):
    store.record_question('student-a', 'doc-a', 'ru')
    store.record_question('student-a', 'doc-b', 'en')
    store.record_question('student-a', 'doc-a', 'ru')
    store.record_question('student-b', 'doc-a', 'en')
    assert store.question_count('student-a') == 3
    assert store.question_count('student-a', 'doc-a') == 2
    assert store.question_count('student-a', 'doc-b') == 1
    assert store.question_count('student-b') == 1
    assert store.question_count('unknown') == 0


def test_records_survive_reopening_without_schema_reset(store):
    store.record_question('anon', 'doc', 'ru')
    reopened = LearningStore(store.path)
    assert reopened.question_count('anon', 'doc') == 1
    reopened.record_question('anon', 'doc', 'en')
    assert store.question_count('anon') == 2


def test_values_are_sql_parameters(store):
    malicious = "'; DROP TABLE question_events; --"
    store.record_question(malicious, malicious, 'ru')
    assert store.question_count(malicious, malicious) == 1
    assert store.question_count('someone else') == 0


def test_concurrent_requests_are_recorded_without_lost_updates(store):
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda _: store.record_question('anon', 'doc', 'ru'), range(32)))
    assert store.question_count('anon', 'doc') == 32


def test_schema_stores_only_event_metadata(store):
    store.record_question('anon', 'doc', 'ru')
    with sqlite3.connect(store.path) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(question_events)')}
        timestamp = connection.execute('SELECT created_at FROM question_events').fetchone()[0]
    assert columns == {'id', 'anon_id', 'doc_id', 'lang', 'created_at'}
    assert timestamp.endswith('+00:00')


def test_corrupt_database_has_sanitized_error(tmp_path):
    path = tmp_path / 'learning.sqlite3'
    path.write_text('not a database; sensitive path details')
    with pytest.raises(LearningStoreError) as error:
        LearningStore(path)
    assert 'sensitive' not in str(error.value)


def test_store_directory_failure_has_sanitized_error(tmp_path):
    path = tmp_path / 'file'
    path.write_text('cannot be a directory')
    with pytest.raises(LearningStoreError):
        LearningStore(path / 'learning.sqlite3')


def test_factory_uses_configured_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    assert get_learning_store().path == (tmp_path / 'learning.sqlite3').resolve()
    assert get_learning_store() is get_learning_store()


@pytest.fixture
def chat_api(tmp_path, monkeypatch):
    from app.translate import Cache
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    doc_id = 'c' * 64
    Cache(tmp_path / 'docs').put(doc_id, {'pages': [{'page': 1, 'original': '', 'translated': 'Attendance is 15%.'}]})
    class FakeLLM:
        def chat(self, system, user):
            return json.dumps({'answer': 'Attendance is 15%.', 'citations': [{'page': 1, 'quote': 'Attendance is 15%.'}]})
    monkeypatch.setattr(main, 'llm', FakeLLM)
    payload = {'anon_id': str(uuid4()), 'doc_id': doc_id, 'lang': 'en', 'question': 'What is attendance?'}
    return TestClient(main.app), payload, get_learning_store()


def test_successful_chat_records_one_event_even_with_long_history(chat_api, monkeypatch):
    client, payload, store = chat_api
    def answer(request, factory):
        return ChatResponse(answer='reply', citations=[], summary='compressed history', kept=8)
    monkeypatch.setattr(main, 'answer_chat', answer)
    payload['history'] = [{'role': 'user', 'content': 'older'}] * 18
    result = client.post('/chat', json=payload)
    assert result.status_code == 200 and result.json()['kept'] == 8
    assert store.question_count(payload['anon_id'], payload['doc_id']) == 1


def test_grounded_and_no_evidence_questions_both_count(chat_api):
    client, payload, store = chat_api
    assert client.post('/chat', json=payload).json()['citations']
    payload['question'] = 'Unicorn recipes?'
    assert client.post('/chat', json=payload).json()['citations'] == []
    assert store.question_count(payload['anon_id']) == 2


def test_unknown_document_and_invalid_request_are_not_recorded(chat_api):
    client, payload, store = chat_api
    assert client.post('/chat', json={**payload, 'doc_id': 'd' * 64}).status_code == 404
    assert client.post('/chat', json={**payload, 'question': ''}).status_code == 422
    assert store.question_count(payload['anon_id']) == 0


def test_llm_failure_is_not_recorded(chat_api, monkeypatch):
    client, payload, store = chat_api
    class InvalidLLM:
        def chat(self, system, user):
            return 'not JSON'
    monkeypatch.setattr(main, 'llm', InvalidLLM)
    assert client.post('/chat', json=payload).status_code == 502
    assert store.question_count(payload['anon_id']) == 0


def test_database_unavailable_returns_503_before_llm(chat_api, monkeypatch):
    client, payload, _ = chat_api
    def fail():
        raise LearningStoreError('sensitive database details')
    def no_llm():
        pytest.fail('Do not spend an LLM call when the database is already unavailable')
    monkeypatch.setattr(main, 'get_learning_store', fail)
    monkeypatch.setattr(main, 'llm', no_llm)
    response = client.post('/chat', json=payload)
    assert response.status_code == 503 and 'sensitive' not in response.text


def test_record_failure_does_not_report_success(chat_api, monkeypatch):
    client, payload, store = chat_api
    def fail(*args):
        raise LearningStoreError('sensitive database details')
    monkeypatch.setattr(store, 'record_question', fail)
    response = client.post('/chat', json=payload)
    assert response.status_code == 503 and 'sensitive' not in response.text
    assert store.question_count(payload['anon_id']) == 0
