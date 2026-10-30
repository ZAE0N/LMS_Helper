import json
from threading import Event, Thread

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, notices, settings
from app.notice_extract import NoticeExtractor
from app.notices import NoticeCrawler, NoticeError, parse_body, parse_list


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        return json.dumps({"summary": "공지 요약", "deadline": None, "deadline_text": None, "action": "내용 확인"})


@pytest.fixture(autouse=True)
def local_extractor(tmp_path, monkeypatch):
    llm = FakeLLM()
    extractor = NoticeExtractor(tmp_path / 'facts', lambda: llm)
    monkeypatch.setattr(notices, 'get_extractor', lambda: extractor)
    monkeypatch.setattr(main, 'get_extractor', lambda: extractor)
    return extractor, llm


def listing(source, ids=(11, 12), total=1):
    rows = []
    for ident in ids:
        href = (f"javascript:jf_combBbs_view('kr','2','17','{ident}');" if source == 'school'
                else f'/bbs/cs/104/{ident}/artclView.do')
        rows.append(f'''<tr><td class="td-subject"><a href="{href}"><strong>공지 {ident}</strong>
                       <span class="new">새글</span></a></td><td class="td-date">2026.10.29.</td></tr>''')
    return '<table><tbody>' + ''.join(rows) + f'</tbody></table><span class="_totPage">{total}</span>'


@pytest.fixture
def setup(tmp_path):
    clock, requests, failures = [10000.0], [], {}
    def sleep(seconds):
        clock[0] += seconds
    def respond(request):
        requests.append((str(request.url), clock[0]))
        assert request.headers['User-Agent'] == 'LMS-Helper/0.1'
        if failures.get('offline'):
            raise httpx.ConnectError('offline', request=request)
        if request.url.path == '/robots.txt':
            return httpx.Response(failures.get('robots_status', 200), text='User-agent: *\nDisallow: /' if failures.get('denied') else 'User-agent: *\nDisallow: /admin')
        source = 'dept' if request.url.host.startswith('cs.') else 'school'
        if request.url.path.endswith(('list.do', 'artclList.do')):
            return httpx.Response(200, text=listing(source))
        return httpx.Response(200, text='<div class="view-con">본문<script>secret()</script></div>')
    client = httpx.Client(transport=httpx.MockTransport(respond))
    crawler = NoticeCrawler(tmp_path / 'notices', client, now=lambda: clock[0], sleep=sleep)
    return crawler, clock, requests, failures


@pytest.mark.parametrize('source', ['school', 'dept'])
def test_parsers(source):
    items, total = parse_list(listing(source), source)
    assert total == 1 and len(items) == 2
    assert items[0]['id'] == f'{source}-11' and items[0]['title'] == '공지 11'
    assert items[0]['posted_at'] == '2026-10-29T00:00:00+09:00'
    assert parse_body('<div class="view-con">본문<script>bad()</script></div>') == '본문'


@pytest.mark.parametrize('bad_date', ['어제', '2026.02.30.', ''])
def test_bad_date_skips_only_that_row(bad_date):
    html = listing('school').replace('2026.10.29.', bad_date, 1)
    rows, _ = parse_list(html, 'school')
    assert [row['id'] for row in rows] == ['school-12']
    rows, _ = parse_list(listing('school', ids=(11,)).replace('2026.10.29.', bad_date), 'school')
    assert rows == []


@pytest.mark.parametrize('value', ['2026-10-29', '2026/10/29', '2026.10.29.'])
def test_date_formats(value):
    rows, _ = parse_list(listing('dept').replace('2026.10.29.', value), 'dept')
    assert len(rows) == 2


def test_cache_pagination_spacing_and_no_repeat_llm(setup, local_extractor):
    crawler, clock, requests, _ = setup
    assert crawler.refresh('school')
    first = crawler.listing('school', 1, 1)
    assert first['has_more'] and first['items'][0]['id'] == 'school-12'
    count, llm_calls = len(requests), local_extractor[1].calls
    second = crawler.listing('school', 1, 2)
    assert not second['has_more'] and second['items'][0]['id'] == 'school-11'
    assert not crawler.refresh('school') and len(requests) == count
    assert all(b[1] - a[1] >= 1 for a, b in zip(requests, requests[1:]))
    clock[0] += 1800
    assert crawler.refresh('school')
    assert len(requests) == count + 2 and local_extractor[1].calls == llm_calls
    restarted = NoticeCrawler(crawler.root, crawler.client, now=lambda: clock[0])
    assert restarted.listing('school', 10, 1)['items'] == crawler.listing('school', 10, 1)['items']
    assert not restarted.refresh('school')


@pytest.mark.parametrize('status', [404, 200])
def test_robots_missing_or_allowed(setup, status):
    crawler, _, _, failures = setup
    failures['robots_status'] = status
    assert crawler.refresh('dept')
    assert len(crawler.listing('dept', 10, 1)['items']) == 2


@pytest.mark.parametrize('mode', ['denied', 'server_error'])
def test_robots_fail_closed_except_404(setup, mode):
    crawler, _, requests, failures = setup
    failures.update({'denied': True} if mode == 'denied' else {'robots_status': 500})
    assert not crawler.refresh('dept')
    assert not crawler.refresh('dept') and len(requests) == 1
    with pytest.raises(NoticeError):
        crawler.listing('dept', 10, 1)


def test_stale_cache_visible_during_failure_and_retry_after_five_minutes(setup):
    crawler, clock, requests, failures = setup
    crawler.refresh('school')
    original = crawler.listing('school', 10, 1)
    clock[0] += 1800
    failures['offline'] = True
    assert not crawler.refresh('school')
    assert crawler.listing('school', 10, 1) == original
    count = len(requests)
    failures.clear()
    clock[0] += 299
    assert not crawler.refresh('school') and len(requests) == count
    clock[0] += 1
    assert crawler.refresh('school')
    assert crawler.listing('school', 10, 1) == original


def test_cold_failure_and_recovery(setup, monkeypatch):
    crawler, clock, _, failures = setup
    monkeypatch.setattr(main, 'get_crawler', lambda: crawler)
    failures['offline'] = True
    crawler.refresh('school')
    response = TestClient(main.app).get('/notices?source=school')
    assert response.status_code == 503 and isinstance(response.json()['detail'], str)
    clock[0] += 300
    failures.clear()
    assert crawler.refresh('school')
    assert TestClient(main.app).get('/notices?source=school').status_code == 200


def test_failed_extraction_reuses_downloaded_body_on_retry(setup, local_extractor):
    crawler, clock, requests, _ = setup
    extractor, llm = local_extractor
    original_chat = llm.chat
    def fail(system, user):
        raise httpx.ConnectError('offline')
    llm.chat = fail
    assert not crawler.refresh('school')
    assert len([url for url, _ in requests if url.endswith('view.do')]) == 1
    clock[0] += 300
    llm.chat = original_chat
    assert crawler.refresh('school')
    assert len([url for url, _ in requests if url.endswith('view.do')]) == 2
    assert len(crawler.listing('school', 10, 1)['items']) == 2


def test_empty_success_is_available_during_later_failure(setup, monkeypatch):
    crawler, clock, *_ = setup
    monkeypatch.setattr(crawler, '_refresh', lambda source: [])
    assert crawler.refresh('school')
    clock[0] += 1800
    def fail(source):
        raise NoticeError('offline')
    monkeypatch.setattr(crawler, '_refresh', fail)
    assert not crawler.refresh('school')
    assert crawler.listing('school', 10, 1) == {'items': [], 'page': 1, 'has_more': False}


@pytest.mark.parametrize('query', ['source=course', 'source=dept&limit=31', 'source=dept&limit=0', 'source=dept&page=0'])
def test_api_validation(query):
    assert TestClient(main.app).get('/notices?' + query).status_code == 422


def test_notice_api(setup, monkeypatch):
    crawler, *_ = setup
    crawler.refresh('dept')
    monkeypatch.setattr(main, 'get_crawler', lambda: crawler)
    response = TestClient(main.app).get('/notices?source=dept&limit=1&page=1')
    assert response.status_code == 200
    notice = response.json()['items'][0]
    assert set(notice) == {'id', 'source', 'course', 'title', 'body', 'summary', 'deadline', 'deadline_text', 'action', 'posted_at', 'url'}
    assert notice['body'] == '본문' and notice['summary'] == '공지 요약'
    assert notice['action'] == '내용 확인' and notice['deadline'] is None
    params = main.app.openapi()['paths']['/notices']['get']['parameters']
    assert 'lang' not in {p['name'] for p in params}


def test_parser_errors():
    with pytest.raises(NoticeError):
        parse_list('<html>blocked</html>', 'school')
    with pytest.raises(NoticeError):
        parse_body('<html>blocked</html>')


def test_upstream_pages_deduplicated_and_sorted(tmp_path):
    requests = []
    def respond(request):
        requests.append(request.url)
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nDisallow: /admin')
        if request.url.path.endswith('list.do'):
            page = int(request.url.params['page'])
            return httpx.Response(200, text=listing('school', (12, 11) if page == 1 else (11, 10), 2))
        return httpx.Response(200, text='<div class="view-con">본문</div>')
    crawler = NoticeCrawler(tmp_path, httpx.Client(transport=httpx.MockTransport(respond)), sleep=lambda _: None)
    crawler.refresh('school')
    response = crawler.listing('school', 30, 1)
    assert [n['id'] for n in response['items']] == ['school-12', 'school-11', 'school-10']
    assert sum(url.path.endswith('view.do') for url in requests) == 3


def test_listing_does_not_wait_for_refresh(setup, monkeypatch):
    crawler, clock, *_ = setup
    crawler.refresh('school')
    previous = crawler.listing('school', 10, 1)
    clock[0] += 1800
    entered, release = Event(), Event()
    def slow_refresh(source):
        entered.set()
        assert release.wait(3)
        return previous['items']
    monkeypatch.setattr(crawler, '_refresh', slow_refresh)
    worker = Thread(target=crawler.refresh, args=('school',))
    worker.start()
    try:
        assert entered.wait(2)
        assert crawler.listing('school', 10, 1) == previous
        assert not crawler.refresh('school')
    finally:
        release.set()
        worker.join(3)


def test_startup_starts_both_boards_and_keeps_health_responsive(setup, monkeypatch):
    crawler, *_ = setup
    entered, release, dept_done = Event(), Event(), Event()
    sources = []
    def slow_refresh(source):
        sources.append(source)
        if source == 'school':
            entered.set()
            assert release.wait(3)
        else:
            dept_done.set()
        return []
    monkeypatch.setattr(crawler, '_refresh', slow_refresh)
    monkeypatch.setattr(main, 'get_crawler', lambda: crawler)
    with TestClient(main.app) as client:
        try:
            assert entered.wait(2)
            assert client.get('/health').json() == {'ok': True}
            assert client.get('/notices?source=school').status_code == 503
        finally:
            release.set()
        assert dept_done.wait(2)
    assert sources == ['school', 'dept']
    assert not crawler.worker.is_alive()


def test_interrupted_refresh_is_not_reported_as_empty_success(setup):
    crawler, *_ = setup
    crawler._write('school', {'attempted_at': crawler.now(), 'error': 'in progress'})
    with pytest.raises(NoticeError):
        crawler.listing('school', 10, 1)


def test_dotenv_path_and_existing_environment(tmp_path, monkeypatch):
    import os
    root = tmp_path / 'server'
    (root / 'app').mkdir(parents=True)
    (root / '.env').write_text('LMS_HELPER_TEST_ENV=loaded\nLMS_HELPER_TEST_EXISTING=file\n')
    monkeypatch.setattr(settings, '__file__', str(root / 'app' / 'settings.py'))
    monkeypatch.delenv('LMS_HELPER_TEST_ENV', raising=False)
    monkeypatch.setenv('LMS_HELPER_TEST_EXISTING', 'shell')
    monkeypatch.chdir(tmp_path)
    settings.load_environment()
    assert os.environ['LMS_HELPER_TEST_ENV'] == 'loaded'
    assert os.environ['LMS_HELPER_TEST_EXISTING'] == 'shell'
    monkeypatch.delenv('LMS_HELPER_TEST_ENV')


def test_russian_glossary_complete():
    from app import glossary, service
    terms = glossary.load(service.glossary_paths('secure-coding'))
    assert not glossary.needs_review(terms, 'ru')
    assert all(t.translations.get('ru') for t in terms if t.rule != 'keep')
