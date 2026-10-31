import copy
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.learning import LearningStoreError, get_learning_store
from app.report import NO_ACTIVITY, SELECT_DOCUMENT, report_sources
from app.translate import Cache

DOC_A, DOC_B = 'a' * 64, 'b' * 64
HASH = 'Cryptographic hashes link blocks.'
MERKLE = 'A Merkle tree summarizes transactions.'


class FakeLLM:
    def __init__(self):
        self.outputs = []
        self.calls = []

    def chat(self, system, user):
        payload = json.loads(user)
        self.calls.append((system, payload))
        result = self.outputs.pop(0) if self.outputs else {
            'summary_tr': 'Review the documented weak concepts.',
            'exam_points': ([{'point_tr': 'Hash links', 'why_tr': 'A definition in the document.', 'pages': [1],
                             'evidence': [{'page': 1, 'quote': HASH}]}] if payload['exam_points_allowed'] else []),
        }
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, str) else json.dumps(result)


def completed_quiz(store, anon, doc, entries):
    quiz_id = str(uuid4())
    questions = [{'id': str(uuid4()), 'type': kind, 'page': page} for kind, page, _ in entries]
    store.save_quiz(quiz_id, anon, doc, 'en', questions)
    for q, (_, _, correct) in zip(questions, entries):
        if q['type'] == 'short':
            store.save_short_grade(quiz_id, q['id'], 'hash', 'en', 100 if correct else 0, correct, 'Feedback')
    store.submit_quiz_result(quiz_id, anon, {q['id']: entry[2] for q, entry in zip(questions, entries)})
    return quiz_id


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    documents = Cache(tmp_path / 'docs')
    documents.put(DOC_A, {'lang': 'en', 'pages': [
        {'page': 1, 'original': HASH, 'translated': HASH}, {'page': 2, 'original': MERKLE, 'translated': MERKLE}]})
    documents.put(DOC_B, {'lang': 'en', 'pages': [{'page': 1, 'original': 'Peer-to-peer networking.', 'translated': ''}]})
    owner, other = str(uuid4()), str(uuid4())
    store = get_learning_store()
    completed_quiz(store, owner, DOC_A, [('multiple', 1, True), ('short', 2, False), ('matching', 2, False)])
    completed_quiz(store, owner, DOC_A, [('multiple', 1, False)])
    completed_quiz(store, owner, DOC_B, [('matching', 1, True)])
    store.save_quiz(str(uuid4()), owner, DOC_A, 'ru', [{'id': str(uuid4()), 'type': 'multiple', 'page': 1}])
    completed_quiz(store, other, DOC_A, [('multiple', 1, True)])
    for doc in (DOC_A, DOC_A, DOC_B):
        store.record_question(owner, doc, 'en')
    for _ in range(5):
        store.record_question(other, DOC_A, 'ru')
    fake = FakeLLM()
    monkeypatch.setattr(main, 'llm', lambda: fake)
    return TestClient(main.app), store, owner, fake


def test_database_statistics_are_weighted_scoped_and_exclude_unsubmitted_quizzes(setup):
    _, store, owner, _ = setup
    stats = store.report_statistics(owner)
    assert stats['quizzes_taken'] == 3 and stats['questions_asked'] == 3
    assert stats['accuracy'] == pytest.approx(2 / 5)
    assert stats['accuracy_by_type'] == {'multiple': 0.5, 'short': 0.0, 'matching': 0.5}
    assert stats['weak_pages'] == [{'doc_id': DOC_A, 'page': 2, 'wrong': 2}, {'doc_id': DOC_A, 'page': 1, 'wrong': 1}]
    assert stats['document_ids'] == [DOC_A, DOC_B]
    filtered = store.report_statistics(owner, DOC_A)
    assert filtered['quizzes_taken'] == 2 and filtered['questions_asked'] == 2
    assert filtered['accuracy'] == 0.25
    assert filtered['accuracy_by_type']['matching'] == 0.0


def test_doc_report_contract_and_grounded_exam_points(setup):
    client, _, owner, fake = setup
    response = client.get('/report', params={'anon_id': owner, 'doc_id': DOC_A, 'lang': 'ru'})
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {'quizzes_taken', 'accuracy', 'accuracy_by_type', 'questions_asked', 'weak_pages', 'exam_points', 'summary_tr'}
    assert result['accuracy'] == 0.25 and result['quizzes_taken'] == 2
    assert result['exam_points'] == [{'point_tr': 'Hash links', 'why_tr': 'A definition in the document.', 'pages': [1]}]
    assert 'Russian' in fake.calls[0][0]
    sent = fake.calls[0][1]
    assert sent['statistics']['wrong_pages'] == [{'page': 2, 'wrong': 2}, {'page': 1, 'wrong': 1}]
    assert owner not in json.dumps(sent) and DOC_A not in json.dumps(sent)


def test_model_cannot_overwrite_database_numbers(setup):
    client, _, owner, fake = setup
    fake.outputs = [{'summary_tr': 'Summary', 'exam_points': [], 'accuracy': 1.0, 'quizzes_taken': 900}]
    result = client.get('/report', params={'anon_id': owner, 'doc_id': DOC_A}).json()
    assert result['accuracy'] == 0.25 and result['quizzes_taken'] == 2


def test_model_receives_attempt_counts_to_distinguish_zero_from_unattempted_types(setup):
    client, store, _, fake = setup
    owner = str(uuid4())
    completed_quiz(store, owner, DOC_A, [('multiple', 1, False)])
    assert client.get('/report', params={'anon_id': owner, 'doc_id': DOC_A}).status_code == 200
    counts = fake.calls[0][1]['statistics']['answered_by_type']
    assert counts == {'multiple': 1, 'short': 0, 'matching': 0}


def test_report_cache_failure_is_sanitized_as_503(setup, monkeypatch):
    from app.report import get_report_builder
    client, _, owner, _ = setup
    builder = get_report_builder(service.DATA_DIR / 'reports')
    def fail(*args):
        raise OSError('sensitive filesystem path')
    monkeypatch.setattr(builder.cache, 'put', fail)
    response = client.get('/report', params={'anon_id': owner, 'doc_id': DOC_A})
    assert response.status_code == 503 and 'sensitive' not in response.text


def test_overall_multiple_docs_do_not_emit_ambiguous_page_references(setup):
    client, _, owner, fake = setup
    response = client.get('/report', params={'anon_id': owner, 'lang': 'ru'})
    assert response.status_code == 200
    result = response.json()
    assert result['accuracy'] == pytest.approx(0.4) and result['quizzes_taken'] == 3
    assert result['exam_points'] == [] and SELECT_DOCUMENT['ru'] in result['summary_tr']
    assert fake.calls[0][1]['exam_points_allowed'] is False


def test_overall_single_doc_can_include_topics(setup):
    client, store, _, fake = setup
    owner = str(uuid4())
    store.record_question(owner, DOC_A, 'en')
    result = client.get('/report', params={'anon_id': owner}).json()
    assert result['questions_asked'] == 1 and result['exam_points']
    assert len(fake.calls) == 1


def test_no_activity_returns_zeroes_without_llm(setup):
    client, _, _, fake = setup
    response = client.get('/report', params={'anon_id': str(uuid4()), 'lang': 'ru'})
    assert response.status_code == 200
    result = response.json()
    assert result['accuracy'] == 0.0 and result['quizzes_taken'] == result['questions_asked'] == 0
    assert result['accuracy_by_type'] == {'multiple': 0.0, 'short': 0.0, 'matching': 0.0}
    assert result['weak_pages'] == result['exam_points'] == []
    assert result['summary_tr'] == NO_ACTIVITY['ru'] and fake.calls == []


def test_selected_document_can_provide_topics_before_activity(setup):
    client, _, _, fake = setup
    result = client.get('/report', params={'anon_id': str(uuid4()), 'doc_id': DOC_A}).json()
    assert result['quizzes_taken'] == 0 and result['exam_points']
    assert len(fake.calls) == 1


def test_report_cache_and_new_learning_activity_invalidate_cache(setup):
    client, store, owner, fake = setup
    params = {'anon_id': owner, 'doc_id': DOC_A}
    first = client.get('/report', params=params)
    assert client.get('/report', params=params).json() == first.json() and len(fake.calls) == 1
    store.record_question(owner, DOC_A, 'en')
    result = client.get('/report', params=params).json()
    assert result['questions_asked'] == 3 and len(fake.calls) == 2
    completed_quiz(store, owner, DOC_A, [('multiple', 1, True)])
    assert client.get('/report', params=params).json()['quizzes_taken'] == 3 and len(fake.calls) == 3


def bad_report(case):
    result = {'summary_tr': 'Summary', 'exam_points': [{'point_tr': 'Topic', 'why_tr': 'Definition', 'pages': [1],
                                                     'evidence': [{'page': 1, 'quote': HASH}]}]}
    point = result['exam_points'][0]
    if case == 'quote':
        point['evidence'][0]['quote'] = 'Invented quote'
    elif case == 'page':
        point.update(pages=[99], evidence=[{'page': 99, 'quote': HASH}])
    elif case == 'unbacked_page':
        point['pages'] = [1, 2]
    elif case == 'empty_quote':
        point['evidence'][0]['quote'] = ' '
    elif case == 'duplicate_pages':
        point['pages'] = [1, 1]
    elif case == 'duplicate_points':
        result['exam_points'].append(copy.deepcopy(point))
    elif case == 'empty_summary':
        result['summary_tr'] = ' '
    return result


@pytest.mark.parametrize('case', ['quote', 'page', 'unbacked_page', 'empty_quote', 'duplicate_pages', 'duplicate_points', 'empty_summary'])
def test_invalid_model_report_retried_without_exposing_bad_points(setup, case):
    client, _, owner, fake = setup
    fake.outputs = [bad_report(case)]
    response = client.get('/report', params={'anon_id': owner, 'doc_id': DOC_A})
    assert response.status_code == 200 and len(fake.calls) == 2
    assert 'retry' in fake.calls[1][1]


def test_ambiguous_topics_in_overall_report_are_rejected(setup):
    client, _, owner, fake = setup
    fake.outputs = [bad_report('empty_summary'), bad_report('quote')]
    assert client.get('/report', params={'anon_id': owner}).status_code == 502


def test_two_bad_outputs_and_http_failure_return_sanitized_502(setup):
    client, _, owner, fake = setup
    fake.outputs = ['not JSON', '{}']
    assert client.get('/report', params={'anon_id': owner, 'doc_id': DOC_A}).status_code == 502
    fake.outputs = [httpx.ConnectError('sensitive upstream details')]
    response = client.get('/report', params={'anon_id': owner, 'doc_id': DOC_A})
    assert response.status_code == 502 and 'sensitive' not in response.text


@pytest.mark.parametrize('params', [{'anon_id': 'bad'}, {'anon_id': str(uuid4()), 'lang': 'ja'},
                                  {'anon_id': str(uuid4()), 'doc_id': '../secret'}])
def test_report_input_validation(setup, params):
    client, _, _, fake = setup
    assert client.get('/report', params=params).status_code == 422 and fake.calls == []


def test_unknown_selected_doc_is_404(setup):
    client, _, owner, fake = setup
    assert client.get('/report', params={'anon_id': owner, 'doc_id': 'f' * 64}).status_code == 404
    assert fake.calls == []


def test_missing_cached_document_does_not_erase_overall_statistics(setup):
    client, store, _, fake = setup
    owner = str(uuid4())
    store.record_question(owner, 'f' * 64, 'en')
    result = client.get('/report', params={'anon_id': owner}).json()
    assert result['questions_asked'] == 1 and result['exam_points'] == []
    assert fake.calls[0][1]['document_excerpts'] == []


def test_source_selection_prioritizes_weak_pages_and_stays_bounded():
    document = {'lang': 'ru', 'pages': [{'page': i, 'original': '원문 ' * 5000, 'translated': 'Перевод ' * 5000} for i in range(1, 61)]}
    sources = report_sources(document, 'ru', [{'doc_id': DOC_A, 'page': 49, 'wrong': 3}], DOC_A)
    assert len(sources) <= 12 and 49 in {s['page'] for s in sources}
    assert sum(len(s.get('original', '')) + len(s.get('translated', '')) for s in sources) <= 24000


def test_database_failure_returns_503(setup, monkeypatch):
    client, store, owner, _ = setup
    def fail(*args):
        raise LearningStoreError('sensitive details')
    monkeypatch.setattr(store, 'report_statistics', fail)
    response = client.get('/report', params={'anon_id': owner})
    assert response.status_code == 503 and 'sensitive' not in response.text
