import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, service
from app.learning import LearningStore, LearningStoreError, QuizConflict, get_learning_store


class FakeLLM:
    def __init__(self):
        self.outputs = []
        self.calls = []

    def chat(self, system, user):
        self.calls.append((system, json.loads(user)))
        result = self.outputs.pop(0) if self.outputs else {'score': 85, 'reason_tr': 'The main idea is correct.'}
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, str) else json.dumps(result)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    store = get_learning_store()
    owner, quiz_id = str(uuid4()), str(uuid4())
    ids = {kind: str(uuid4()) for kind in ('multiple', 'short', 'matching')}
    questions = [
        {'id': ids['multiple'], 'type': 'multiple', 'question': 'Which value?', 'explanation': 'It is one.', 'page': 7,
         'choices': ['One', 'Two', 'Three', 'Four'], 'answer_index': 0},
        {'id': ids['short'], 'type': 'short', 'question': 'What percent is the exam?', 'explanation': 'The exam is 35%.', 'page': 8,
         'model_answer': '35%', 'keywords': ['exam', 'percent']},
        {'id': ids['matching'], 'type': 'matching', 'question': 'Match terms.', 'explanation': 'These are pairs.', 'page': 9,
         'pairs': [['A', 'One'], ['B', 'Two']]},
    ]
    store.save_quiz(quiz_id, owner, 'a' * 64, 'en', questions)
    fake = FakeLLM()
    monkeypatch.setattr(main, 'llm', lambda: fake)
    grade = {'lang': 'en', 'quiz_id': quiz_id, 'question_id': ids['short'], 'user_answer': 'Thirty-five percent'}
    result = {'anon_id': owner, 'quiz_id': quiz_id, 'answers': [{'id': q['id'], 'correct': True} for q in questions]}
    return TestClient(main.app), store, fake, grade, result, ids


def test_short_grade_uses_reference_and_language(setup):
    client, store, fake, grade, _, ids = setup
    grade['lang'] = 'ru'
    response = client.post('/quiz/grade-short', json=grade)
    assert response.status_code == 200
    assert response.json() == {'score': 85, 'is_correct': True, 'reason_tr': 'The main idea is correct.'}
    assert 'Russian' in fake.calls[0][0]
    sent = fake.calls[0][1]
    assert sent['model_answer'] == '35%' and sent['keywords'] == ['exam', 'percent']
    cached = store.get_short_grade(grade['quiz_id'], ids['short'])
    assert cached['answer_hash'] == hashlib.sha256(grade['user_answer'].encode()).hexdigest()
    assert 'user_answer' not in cached


@pytest.mark.parametrize(('answer', 'score', 'correct'), [('', 0, False), ('  ', 0, False), ('35%', 100, True), ('  35%  ', 100, True)])
def test_blank_and_exact_answers_do_not_call_llm(setup, answer, score, correct):
    client, _, fake, grade, *_ = setup
    grade['user_answer'] = answer
    response = client.post('/quiz/grade-short', json=grade)
    assert response.status_code == 200 and response.json()['score'] == score
    assert response.json()['is_correct'] is correct
    assert fake.calls == []


@pytest.mark.parametrize(('score', 'correct'), [(0, False), (69, False), (70, True), (100, True)])
def test_correctness_is_derived_from_score(setup, score, correct):
    client, _, fake, grade, *_ = setup
    fake.outputs = [{'score': score, 'is_correct': not correct, 'reason_tr': 'Feedback'}]
    response = client.post('/quiz/grade-short', json=grade)
    assert response.status_code == 200 and response.json()['is_correct'] is correct


def test_repeated_last_answer_is_cached_even_after_restart(setup, monkeypatch):
    client, store, fake, grade, *_ = setup
    first = client.post('/quiz/grade-short', json=grade)
    monkeypatch.setattr(main, 'get_learning_store', lambda: LearningStore(store.path))
    second = client.post('/quiz/grade-short', json=grade)
    assert first.json() == second.json() and len(fake.calls) == 1


def test_new_answer_replaces_grade_before_submission(setup):
    client, store, fake, grade, _, ids = setup
    fake.outputs = [{'score': 0, 'reason_tr': 'Incorrect.'}]
    assert not client.post('/quiz/grade-short', json=grade).json()['is_correct']
    grade['user_answer'] = '35%'
    assert client.post('/quiz/grade-short', json=grade).json()['score'] == 100
    assert store.get_short_grade(grade['quiz_id'], ids['short'])['score'] == 100


def test_exact_match_does_not_erase_mathematical_or_code_differences(setup):
    client, store, fake, grade, result, _ = setup
    record = store.get_quiz(result['quiz_id'])
    short = next(q for q in record['questions'] if q['type'] == 'short')
    short['model_answer'] = 'x2'
    quiz_id = str(uuid4())
    store.save_quiz(quiz_id, record['anon_id'], record['doc_id'], 'en', [short])
    fake.outputs = [{'score': 0, 'reason_tr': 'A superscript changes the expression.'}]
    grade.update(quiz_id=quiz_id, user_answer='x²')
    assert client.post('/quiz/grade-short', json=grade).json()['score'] == 0
    assert len(fake.calls) == 1


@pytest.mark.parametrize(('field', 'value'), [('lang', 'ja'), ('quiz_id', 'bad'), ('question_id', 'bad'), ('user_answer', 'x' * 8001)])
def test_grade_input_validation(setup, field, value):
    client, _, fake, grade, *_ = setup
    grade[field] = value
    assert client.post('/quiz/grade-short', json=grade).status_code == 422
    assert fake.calls == []


def test_unknown_quiz_question_and_wrong_type(setup):
    client, _, fake, grade, _, ids = setup
    assert client.post('/quiz/grade-short', json={**grade, 'quiz_id': str(uuid4())}).status_code == 404
    assert client.post('/quiz/grade-short', json={**grade, 'question_id': str(uuid4())}).status_code == 404
    assert client.post('/quiz/grade-short', json={**grade, 'question_id': ids['multiple']}).status_code == 400
    assert fake.calls == []


@pytest.mark.parametrize('bad', ['not JSON', {'score': 101, 'reason_tr': 'Bad'}, {'score': -1, 'reason_tr': 'Bad'},
                                 {'score': True, 'reason_tr': 'Bad'}, {'score': 70.5, 'reason_tr': 'Bad'}, {'score': 70, 'reason_tr': ' '}])
def test_invalid_grade_output_is_retried_once(setup, bad):
    client, _, fake, grade, *_ = setup
    fake.outputs = [bad]
    assert client.post('/quiz/grade-short', json=grade).status_code == 200
    assert len(fake.calls) == 2


def test_two_invalid_grades_return_502_without_saving(setup):
    client, store, fake, grade, _, ids = setup
    fake.outputs = ['not JSON', '{}']
    assert client.post('/quiz/grade-short', json=grade).status_code == 502
    assert store.get_short_grade(grade['quiz_id'], ids['short']) is None


def test_upstream_grade_error_is_sanitized_and_not_retried(setup):
    client, _, fake, grade, *_ = setup
    fake.outputs = [httpx.ConnectError('sensitive upstream details')]
    response = client.post('/quiz/grade-short', json=grade)
    assert response.status_code == 502 and 'sensitive' not in response.text and len(fake.calls) == 1


def test_result_uses_server_short_grade_not_client_flag(setup):
    client, store, fake, grade, result, ids = setup
    fake.outputs = [{'score': 0, 'reason_tr': 'Incorrect.'}]
    client.post('/quiz/grade-short', json=grade)
    response = client.post('/quiz/result', json=result)
    assert response.status_code == 200 and response.json() == {'score': 2, 'total': 3}
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute('SELECT question_id, type, page, correct FROM quiz_answer_results').fetchall()
    assert (ids['short'], 'short', 8, 0) in rows
    assert len(rows) == 3


def test_repeat_result_is_idempotent_in_any_answer_order(setup):
    client, store, _, grade, result, _ = setup
    grade['user_answer'] = '35%'
    client.post('/quiz/grade-short', json=grade)
    first = client.post('/quiz/result', json=result)
    result['answers'].reverse()
    second = client.post('/quiz/result', json=result)
    assert first.json() == second.json() == {'score': 3, 'total': 3}
    assert LearningStore(store.path).get_quiz_result(grade['quiz_id']) == first.json()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM quiz_results').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM quiz_answer_results').fetchone()[0] == 3


def test_changed_result_is_rejected_without_mutation(setup):
    client, store, _, grade, result, ids = setup
    grade['user_answer'] = '35%'
    client.post('/quiz/grade-short', json=grade)
    client.post('/quiz/result', json=result)
    result['answers'][0]['correct'] = False
    assert client.post('/quiz/result', json=result).status_code == 409
    assert store.get_quiz_result(grade['quiz_id']) == {'score': 3, 'total': 3}


def test_finalized_quiz_cannot_be_regraded_but_last_response_can_be_retried(setup):
    client, store, fake, grade, result, _ = setup
    grade['user_answer'] = '35%'
    first = client.post('/quiz/grade-short', json=grade)
    client.post('/quiz/result', json=result)
    assert client.post('/quiz/grade-short', json=grade).json() == first.json()
    grade['user_answer'] = 'wrong new answer'
    assert client.post('/quiz/grade-short', json=grade).status_code == 409
    assert store.get_quiz_result(grade['quiz_id']) == {'score': 3, 'total': 3} and fake.calls == []


def test_missing_short_grade_rejected_without_partial_result(setup):
    client, store, _, grade, result, _ = setup
    assert client.post('/quiz/result', json=result).status_code == 400
    assert store.get_quiz_result(grade['quiz_id']) is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM quiz_answer_results').fetchone()[0] == 0


def test_result_owner_and_known_question_set_are_checked(setup):
    client, store, _, grade, result, _ = setup
    assert client.post('/quiz/result', json={**result, 'anon_id': str(uuid4())}).status_code == 403
    assert client.post('/quiz/result', json={**result, 'quiz_id': str(uuid4())}).status_code == 404
    assert client.post('/quiz/result', json={**result, 'answers': result['answers'][:-1]}).status_code == 400
    result['answers'][0]['id'] = str(uuid4())
    assert client.post('/quiz/result', json=result).status_code == 400
    assert store.get_quiz_result(grade['quiz_id']) is None


def test_result_validation_rejects_duplicate_and_nonboolean_flags(setup):
    client, _, _, _, result, _ = setup
    assert client.post('/quiz/result', json={**result, 'answers': []}).status_code == 422
    assert client.post('/quiz/result', json={**result, 'answers': [result['answers'][0]] * 2}).status_code == 422
    result['answers'][0]['correct'] = 1
    assert client.post('/quiz/result', json=result).status_code == 422


def test_multiple_and_matching_only_quiz_needs_no_server_grading(setup):
    client, store, _, _, result, _ = setup
    record = store.get_quiz(result['quiz_id'])
    new_id = str(uuid4())
    subset = [q for q in record['questions'] if q['type'] != 'short']
    store.save_quiz(new_id, result['anon_id'], record['doc_id'], 'en', subset)
    response = client.post('/quiz/result', json={**result, 'quiz_id': new_id, 'answers': [{'id': q['id'], 'correct': False} for q in subset]})
    assert response.status_code == 200 and response.json() == {'score': 0, 'total': 2}


def test_concurrent_duplicate_submissions_create_one_result(setup):
    client, store, _, grade, result, _ = setup
    grade['user_answer'] = '35%'
    client.post('/quiz/grade-short', json=grade)
    answers = {a['id']: a['correct'] for a in result['answers']}
    with ThreadPoolExecutor(max_workers=6) as workers:
        responses = list(workers.map(lambda _: store.submit_quiz_result(result['quiz_id'], result['anon_id'], answers), range(12)))
    assert all(r == {'score': 3, 'total': 3} for r in responses)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM quiz_results').fetchone()[0] == 1


def test_result_transaction_rolls_back_if_answer_rows_cannot_be_saved(setup):
    client, store, _, grade, result, _ = setup
    grade['user_answer'] = '35%'
    client.post('/quiz/grade-short', json=grade)
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TRIGGER reject_answer BEFORE INSERT ON quiz_answer_results BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    assert client.post('/quiz/result', json=result).status_code == 503
    assert store.get_quiz_result(result['quiz_id']) is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM quiz_answer_results').fetchone()[0] == 0


def test_storage_error_is_sanitized(setup, monkeypatch):
    client, store, _, grade, result, _ = setup
    def fail(*args):
        raise LearningStoreError('sensitive details')
    monkeypatch.setattr(store, 'save_short_grade', fail)
    grade['user_answer'] = '35%'
    response = client.post('/quiz/grade-short', json=grade)
    assert response.status_code == 503 and 'sensitive' not in response.text
    monkeypatch.setattr(store, 'submit_quiz_result', fail)
    response = client.post('/quiz/result', json=result)
    assert response.status_code == 503 and 'sensitive' not in response.text


def test_short_grade_cannot_commit_after_result_is_finalized(setup):
    client, store, _, grade, result, ids = setup
    grade['user_answer'] = '35%'
    client.post('/quiz/grade-short', json=grade)
    client.post('/quiz/result', json=result)
    with pytest.raises(QuizConflict):
        store.save_short_grade(result['quiz_id'], ids['short'], 'newhash', 'en', 0, False, 'Changed')
    assert store.get_short_grade(result['quiz_id'], ids['short'])['score'] == 100
