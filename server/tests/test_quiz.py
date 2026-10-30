import copy
import json
import sqlite3
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.learning import LearningStore, LearningStoreError, get_learning_store
from app.quiz import MAX_SOURCE_PAGES, SOURCE_BUDGET, QuizRequest, generation_schema, source_pages
from app.translate import Cache

DOC_ID = 'e' * 64
TEXT = 'Hashes link blocks. Ledgers store transactions.'


def questions(payload):
    page = payload['pages'][0]
    evidence = next(page[k] for k in ('original', 'translated') if page.get(k))
    result = []
    for i in range(payload['count']):
        kind = payload['types'][i % len(payload['types'])]
        item = {'type': kind, 'question': f'Question about hashes {i + 1}?', 'explanation': 'The document explains hashes and ledgers.', 'page': page['page'], 'evidence': evidence}
        if kind == 'multiple':
            item.update(choices=['Hashes', 'Clouds', 'Bananas', 'Planets'], answer_index=0)
        elif kind == 'short':
            item.update(model_answer='Hashes link blocks.', keywords=['hashes', 'blocks'])
        else:
            item.update(pairs=[['Hash', 'Links blocks'], ['Ledger', 'Stores transactions']])
        result.append(item)
    return {'questions': result}


class FakeLLM:
    def __init__(self, outputs=None):
        self.calls = []
        self.outputs = outputs or []

    def chat(self, system, user):
        payload = json.loads(user)
        self.calls.append((system, payload))
        if len(self.calls) <= len(self.outputs):
            output = self.outputs[len(self.calls) - 1]
            output = output(payload) if callable(output) else output
        else:
            output = questions(payload)
        if isinstance(output, Exception):
            raise output
        return output if isinstance(output, str) else json.dumps(output)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    Cache(tmp_path / 'docs').put(DOC_ID, {'lang': 'en', 'pages': [
        {'page': 1, 'original': TEXT, 'translated': TEXT},
        {'page': 2, 'original': TEXT, 'translated': TEXT},
        {'page': 3, 'original': '', 'translated': ''},
    ]})
    fake = FakeLLM()
    monkeypatch.setattr(main, 'llm', lambda: fake)
    request = {'anon_id': str(uuid4()), 'doc_id': DOC_ID, 'lang': 'en', 'count': 3, 'types': ['multiple', 'short', 'matching'], 'pages': None}
    return TestClient(main.app), request, fake


def test_all_question_types_are_saved_and_response_matches_contract(setup):
    client, request, fake = setup
    response = client.post('/quiz', json=request)
    assert response.status_code == 200
    result = response.json()
    UUID(result['quiz_id'])
    assert len(result['questions']) == 3
    assert {q['type'] for q in result['questions']} == set(request['types'])
    assert len({q['id'] for q in result['questions']}) == 3
    for question in result['questions']:
        UUID(question['id'])
        assert 'evidence' not in question
        assert question['page'] in (1, 2)
    record = get_learning_store().get_quiz(result['quiz_id'])
    assert record['questions'] == result['questions']
    assert record['anon_id'] == request['anon_id'] and record['doc_id'] == DOC_ID and record['lang'] == 'en'
    assert LearningStore(get_learning_store().path).get_quiz(result['quiz_id']) == record
    assert len(fake.calls) == 1
    assert get_learning_store().question_count(request['anon_id']) == 0


@pytest.mark.parametrize('kind', ['multiple', 'short', 'matching'])
def test_single_selected_type_and_russian_prompt(setup, kind):
    client, request, fake = setup
    request.update(types=[kind], lang='ru', count=2)
    result = client.post('/quiz', json=request)
    assert result.status_code == 200
    assert all(q['type'] == kind for q in result.json()['questions'])
    assert 'Russian' in fake.calls[0][0]


def test_defaults_and_fewer_questions_than_selected_types(setup):
    client, request, _ = setup
    request.pop('count')
    request.pop('types')
    assert len(client.post('/quiz', json=request).json()['questions']) == 5
    request['count'] = 1
    assert len(client.post('/quiz', json=request).json()['questions']) == 1


def test_page_range_is_respected_and_not_renumbered(setup):
    client, request, fake = setup
    request['pages'] = [2, 2]
    response = client.post('/quiz', json=request)
    assert response.status_code == 200
    assert {q['page'] for q in response.json()['questions']} == {2}
    assert [p['page'] for p in fake.calls[0][1]['pages']] == [2]


@pytest.mark.parametrize(('field', 'value'), [
    ('count', 0), ('count', 21), ('count', True), ('count', '3'), ('lang', 'ja'), ('anon_id', 'bad'),
    ('doc_id', '../secret'), ('types', []), ('types', ['multiple', 'multiple']), ('types', ['essay']),
    ('pages', [2, 1]), ('pages', [0, 1]), ('pages', [1]), ('pages', [True, 2]),
])
def test_invalid_request_does_not_call_llm(setup, field, value):
    client, request, fake = setup
    request[field] = value
    assert client.post('/quiz', json=request).status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize('pages', [[1, 99], [3, 3]])
def test_invalid_or_empty_page_range_returns_400(setup, pages):
    client, request, fake = setup
    request['pages'] = pages
    assert client.post('/quiz', json=request).status_code == 400
    assert fake.calls == []


def test_unknown_document_returns_404_before_llm(setup):
    client, request, fake = setup
    request['doc_id'] = 'f' * 64
    assert client.post('/quiz', json=request).status_code == 404
    assert fake.calls == []


def invalid_output(case):
    def make(payload):
        result = questions(payload)
        q = result['questions'][0]
        if case == 'count':
            result['questions'].pop()
        elif case == 'page':
            q['page'] = 999
        elif case == 'evidence':
            q['evidence'] = 'Invented text'
        elif case == 'missing_evidence':
            q.pop('evidence')
        elif case == 'choices':
            q['choices'] = ['Only one']
        elif case == 'duplicate_choices':
            q['choices'] = ['Hash', 'hash', 'Three', 'Four']
        elif case == 'index':
            q['answer_index'] = 4
        elif case == 'bool_index':
            q['answer_index'] = True
        elif case == 'empty_question':
            q['question'] = '  '
        elif case == 'extra':
            q['unknown_field'] = 'extra'
        elif case == 'missing_type':
            result['questions'][1] = {**copy.deepcopy(q), 'question': 'Another question?'}
        elif case == 'duplicate_question':
            result['questions'][1]['question'] = q['question']
        elif case == 'keywords':
            result['questions'][1]['keywords'] = []
        elif case == 'matching_left':
            result['questions'][2]['pairs'][1][0] = result['questions'][2]['pairs'][0][0]
        elif case == 'matching_right':
            result['questions'][2]['pairs'][1][1] = result['questions'][2]['pairs'][0][1]
        return result
    return make


@pytest.mark.parametrize('case', ['count', 'page', 'evidence', 'missing_evidence', 'choices', 'duplicate_choices', 'index',
                                  'bool_index', 'empty_question', 'extra', 'missing_type', 'duplicate_question', 'keywords', 'matching_left', 'matching_right'])
def test_invalid_generation_retried_once_then_corrected(setup, case):
    client, request, fake = setup
    fake.outputs = [invalid_output(case)]
    response = client.post('/quiz', json=request)
    assert response.status_code == 200
    assert len(fake.calls) == 2 and 'retry' in fake.calls[1][1]


def test_unselected_question_type_retried(setup):
    client, request, fake = setup
    request.update(types=['short'], count=1)
    def wrong(payload):
        return questions({**payload, 'types': ['multiple']})
    fake.outputs = [wrong]
    assert client.post('/quiz', json=request).status_code == 200
    assert len(fake.calls) == 2


def test_malformed_json_retried(setup):
    client, request, fake = setup
    fake.outputs = ['not JSON']
    assert client.post('/quiz', json=request).status_code == 200
    assert len(fake.calls) == 2


def test_generation_schema_requires_common_fields_for_every_type():
    schema = generation_schema()
    assert len(schema['$defs']) == 3
    for definition in schema['$defs'].values():
        assert {'type', 'question', 'explanation', 'page', 'evidence'} <= set(definition['required'])
        assert 'id' not in definition['properties']
        assert definition['properties']['evidence']['type'] == 'array'


def test_multiple_separate_evidence_quotes_are_supported(setup):
    client, request, fake = setup
    def quoted_lines(payload):
        result = questions(payload)
        for q in result['questions']:
            q['evidence'] = ['Hashes link blocks.', 'Ledgers store transactions.']
        return result
    fake.outputs = [quoted_lines]
    assert client.post('/quiz', json=request).status_code == 200
    assert len(fake.calls) == 1


def test_one_fabricated_quote_invalidates_evidence_array(setup):
    client, request, fake = setup
    def bad_quote(payload):
        result = questions(payload)
        result['questions'][0]['evidence'] = ['Hashes link blocks.', 'Invented source.']
        return result
    fake.outputs = [bad_quote]
    assert client.post('/quiz', json=request).status_code == 200
    assert len(fake.calls) == 2


def test_retry_reports_all_missing_fields_and_duplicate_matching_values(setup):
    client, request, fake = setup
    def omissions(payload):
        result = questions(payload)
        for q in result['questions'][1:]:
            q.pop('evidence')
        result['questions'][1].pop('question')
        result['questions'][2]['pairs'][1][0] = result['questions'][2]['pairs'][0][0]
        return result
    fake.outputs = [omissions]
    assert client.post('/quiz', json=request).status_code == 200
    feedback = fake.calls[1][1]['retry']
    assert 'evidence is REQUIRED' in feedback
    assert 'questions.1.short.question' in feedback
    assert 'questions.2.matching:' in feedback
    assert 'distinct' in feedback
    assert 'REGENERATE' in fake.calls[1][0]
    assert 'previous_output' not in fake.calls[1][1]


def test_equivalent_right_options_are_omitted_without_changing_answers(setup):
    client, request, fake = setup
    request.update(types=['matching'], count=1)
    def duplicate_right(payload):
        result = questions(payload)
        result['questions'][0]['pairs'] = [['A', 'One'], ['B', 'Two'], ['C', 'Two']]
        return result
    fake.outputs = [duplicate_right]
    response = client.post('/quiz', json=request)
    assert response.status_code == 200
    assert response.json()['questions'][0]['pairs'] == [['A', 'One'], ['B', 'Two']]
    assert len(fake.calls) == 1


def test_second_invalid_output_returns_502_and_saves_nothing(setup):
    client, request, fake = setup
    fake.outputs = ['not JSON', {'questions': []}]
    response = client.post('/quiz', json=request)
    assert response.status_code == 502 and 'detail' in response.json()
    assert len(fake.calls) == 2
    with sqlite3.connect(get_learning_store().path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0] == 0


def test_upstream_error_is_not_retried_or_exposed(setup):
    client, request, fake = setup
    fake.outputs = [httpx.ConnectError('sensitive upstream details')]
    response = client.post('/quiz', json=request)
    assert response.status_code == 502 and 'sensitive' not in response.text
    assert len(fake.calls) == 1


def test_model_ids_are_replaced_by_server_ids(setup):
    client, request, fake = setup
    def supplied_ids(payload):
        result = questions(payload)
        for q in result['questions']:
            q['id'] = '../../not-trusted'
        return result
    fake.outputs = [supplied_ids]
    response = client.post('/quiz', json=request)
    assert response.status_code == 200
    for q in response.json()['questions']:
        UUID(q['id'])


def test_quiz_source_context_is_bounded_and_spread_over_range():
    request = QuizRequest(anon_id=uuid4(), doc_id=DOC_ID, lang='ru', pages=(10, 50))
    document = {'lang': 'ru', 'pages': [{'page': i, 'original': '원문 ' * 5000, 'translated': 'Перевод ' * 5000} for i in range(1, 61)]}
    sources = source_pages(document, request)
    assert len(sources) == MAX_SOURCE_PAGES
    assert sources[0]['page'] == 10 and sources[-1]['page'] == 50
    assert sum(len(s.get('original', '')) + len(s.get('translated', '')) for s in sources) <= SOURCE_BUDGET


def test_legacy_learning_database_is_upgraded_without_losing_questions(tmp_path):
    path = tmp_path / 'learning.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE question_events (id INTEGER PRIMARY KEY, anon_id TEXT NOT NULL, doc_id TEXT NOT NULL, lang TEXT NOT NULL, created_at TEXT NOT NULL)')
        connection.execute("INSERT INTO question_events VALUES (1, 'anon', 'doc', 'ru', '2026-10-30')")
    store = LearningStore(path)
    assert store.question_count('anon') == 1
    store.save_quiz('quiz', 'anon', 'doc', 'ru', [])
    assert store.get_quiz('quiz')['questions'] == []
    assert store.get_quiz('unknown') is None
    assert store.get_quiz("'; DROP TABLE quizzes; --") is None


def test_storage_failure_returns_503_without_success(setup, monkeypatch):
    client, request, _ = setup
    store = get_learning_store()
    def fail(*args):
        raise LearningStoreError('sensitive details')
    monkeypatch.setattr(store, 'save_quiz', fail)
    response = client.post('/quiz', json=request)
    assert response.status_code == 503 and 'sensitive' not in response.text
