import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.notice_extract import NoticeExtractor, validated_facts
from app.notices import NoticeCrawler


def facts(deadline='2026-11-03', text='2026년 11월 3일'):
    return json.dumps({'summary': '장학금 신청 안내', 'deadline': deadline, 'deadline_text': text, 'action': '신청서를 제출하세요'}, ensure_ascii=False)


class FakeLLM:
    def __init__(self, output=None):
        self.calls = 0
        self.output = output or facts()

    def chat(self, system, user):
        self.calls += 1
        return self.output


@pytest.mark.parametrize('text', ['2026년 11월 3일', '2026-11-03', '2026.11.03.', '2026/11/3'])
def test_deadline_grounded_in_source(text):
    result = validated_facts(facts(text=text), '신청', f'{text}까지 제출')
    assert result['deadline'] == '2026-11-03' and result['deadline_text'] == text


@pytest.mark.parametrize(('raw', 'body'), [
    (facts(), '기한 없음'),
    (facts('2026-11-04'), '2026년 11월 3일까지'),
    (facts('2026-02-30', '2026년 2월 30일'), '2026년 2월 30일'),
    (facts('2027-11-03', '11월 3일'), '11월 3일'),
    (facts(), '2026-11-03까지'),
    (facts('20261103'), '2026년 11월 3일'),
    (facts(None, None), '마감 없음'),
])
def test_hallucinated_or_unverifiable_deadline_removed(raw, body):
    result = validated_facts(raw, '신청 안내', body)
    assert result['deadline'] is None and result['deadline_text'] is None


def test_cache_survives_restart_and_content_changes(tmp_path):
    fake = FakeLLM()
    extractor = NoticeExtractor(tmp_path, lambda: fake)
    first = extractor.extract('안내', '2026년 11월 3일까지 제출')
    restarted = NoticeExtractor(tmp_path, lambda: fake)
    assert restarted.extract('안내', '2026년 11월 3일까지 제출') == first
    assert fake.calls == 1
    restarted.extract('변경된 안내', '2026년 11월 3일까지 제출')
    assert fake.calls == 2


def test_deadline_can_be_supported_by_title():
    result = validated_facts(facts(), '신청 마감: 2026년 11월 3일', '신청서를 제출하세요')
    assert result['deadline'] == '2026-11-03'


def test_llm_network_error_returns_sanitized_502(tmp_path, monkeypatch):
    class OfflineLLM:
        def chat(self, system, user):
            raise httpx.ConnectError('sensitive upstream details')
    extractor = NoticeExtractor(tmp_path, OfflineLLM)
    monkeypatch.setattr(main, 'get_extractor', lambda: extractor)
    response = TestClient(main.app).post('/notices/extract', json={'items': [{'id': '1', 'title': '', 'body': '공지'}]})
    assert response.status_code == 502
    assert 'sensitive' not in response.text


def test_extract_api_and_crawler_share_content_cache(tmp_path, monkeypatch):
    fake = FakeLLM()
    extractor = NoticeExtractor(tmp_path / 'facts', lambda: fake)
    crawler = NoticeCrawler(tmp_path / 'notices', httpx.Client(), extractor=extractor)
    monkeypatch.setattr(main, 'get_extractor', lambda: extractor)
    crawler.extractor.extract('안내', '2026년 11월 3일까지 제출')
    payload = {'items': [{'id': 'canvas-123', 'title': '안내', 'body': '2026년 11월 3일까지 제출'}]}
    client = TestClient(main.app)
    result = client.post('/notices/extract', json=payload)
    assert result.status_code == 200
    assert result.json() == {'items': [{'id': 'canvas-123', 'summary': '장학금 신청 안내', 'deadline': '2026-11-03', 'deadline_text': '2026년 11월 3일', 'action': '신청서를 제출하세요'}]}
    payload['items'][0]['id'] = 'canvas-other'
    assert client.post('/notices/extract', json=payload).json()['items'][0]['id'] == 'canvas-other'
    assert fake.calls == 1


def test_extract_batch_limits_and_empty_batch(tmp_path, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(main, 'get_extractor', lambda: NoticeExtractor(tmp_path, lambda: fake))
    client = TestClient(main.app)
    item = {'id': '1', 'title': '안내', 'body': '본문'}
    assert client.post('/notices/extract', json={'items': [item] * 30}).status_code == 200
    assert client.post('/notices/extract', json={'items': [item] * 31}).status_code == 422
    assert client.post('/notices/extract', json={'items': []}).json() == {'items': []}
    assert client.post('/notices/extract', json={'items': [{'id': '1'}]}).status_code == 422
    assert fake.calls == 1


@pytest.mark.parametrize('bad_output', ['not JSON', '{}', '{"summary": null}', '[]'])
def test_bad_llm_output_returns_502_and_is_not_cached(tmp_path, monkeypatch, bad_output):
    fake = FakeLLM(bad_output)
    extractor = NoticeExtractor(tmp_path, lambda: fake)
    monkeypatch.setattr(main, 'get_extractor', lambda: extractor)
    client = TestClient(main.app)
    payload = {'items': [{'id': '1', 'title': '안내', 'body': '본문'}]}
    response = client.post('/notices/extract', json=payload)
    assert response.status_code == 502 and 'detail' in response.json()
    fake.output = facts()
    assert client.post('/notices/extract', json=payload).status_code == 200
    assert fake.calls == 2
