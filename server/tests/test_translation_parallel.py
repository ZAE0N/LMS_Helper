import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx
import pytest

from app import main, service
from app.translate import Cache


@pytest.fixture
def pages(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    data = [{'page': i + 1, 'text': f'페이지 내용 {i}'} for i in range(5)]
    monkeypatch.setattr(service, 'extract_pages', lambda _: data)
    return data


def test_parallel_translation_preserves_order_and_respects_worker_limit(pages, monkeypatch):
    active, peak = [0], [0]
    lock = Lock()
    parallel = Event()
    def translate(text, *args):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            if active[0] >= 2:
                parallel.set()
        assert parallel.wait(2)
        with lock:
            active[0] -= 1
        return {'translated': text, 'terms': [], 'issues': []}
    monkeypatch.setattr(service, 'translate_page', translate)
    result = service.translate_pdf(b'fake', 'ru', None, object(), workers=3)
    assert [p['page'] for p in result['pages']] == [1, 2, 3, 4, 5]
    assert 2 <= peak[0] <= 3


def test_duplicate_pages_and_concurrent_same_document_do_not_repeat_translation(pages, monkeypatch):
    for p in pages:
        p['text'] = '동일한 페이지'
    calls = []
    def translate(text, *args):
        calls.append(text)
        return {'translated': 'Same page', 'terms': [], 'issues': []}
    monkeypatch.setattr(service, 'translate_page', translate)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: service.translate_pdf(b'fake', 'ru', None, object(), workers=3), range(3)))
    assert len(calls) == 1 and results[0] == results[1] == results[2]
    assert len(results[0]['pages']) == len(pages)


def test_page_cache_survives_failure_and_is_reused_on_retry(pages, monkeypatch):
    calls = []
    fail = [True]
    def translate(text, *args):
        calls.append(text)
        if text == pages[1]['text'] and fail[0]:
            raise httpx.ConnectError('offline')
        return {'translated': text, 'terms': [], 'issues': []}
    monkeypatch.setattr(service, 'translate_page', translate)
    with pytest.raises(httpx.ConnectError):
        service.translate_pdf(b'fake', 'ru', None, object(), workers=1)
    fail[0] = False
    result = service.translate_pdf(b'fake', 'ru', None, object(), workers=1)
    assert calls.count(pages[0]['text']) == 1 and len(result['pages']) == 5


@pytest.mark.parametrize('workers', [0, 6])
def test_invalid_worker_count_is_rejected(pages, workers):
    with pytest.raises(ValueError):
        service.translate_pdf(b'fake', 'ru', None, object(), workers=workers)


def test_cache_writes_remain_valid_under_concurrency(tmp_path):
    cache = Cache(tmp_path)
    values = [{'number': i, 'text': 'я' * 4000} for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda v: cache.put('same', v), values))
    assert cache.get('same') in values
    assert not list(tmp_path.glob('*.tmp'))


def test_document_translation_does_not_block_async_health(monkeypatch):
    entered, release = Event(), Event()
    def slow(*args):
        entered.set()
        assert release.wait(3)
        return {'doc_id': 'a' * 64, 'pages': []}
    monkeypatch.setattr(service, 'translate_pdf', slow)
    monkeypatch.setattr(main, 'llm', lambda: object())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            task = asyncio.create_task(client.post('/documents', files={'file': ('a.pdf', b'%PDF- fake')}, data={'lang': 'ru'}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                health = await asyncio.wait_for(client.get('/health'), timeout=1)
                assert health.json() == {'ok': True}
            finally:
                release.set()
            assert (await task).status_code == 200
    asyncio.run(run())
