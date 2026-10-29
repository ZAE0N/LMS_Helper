import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, settings
from app.notices import NoticeCrawler, NoticeError, parse_body, parse_list


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
    clock, requests, denied = [10000.0], [], []
    def sleep(seconds):
        clock[0] += seconds
    def respond(request):
        requests.append((str(request.url), clock[0]))
        assert request.headers['User-Agent'] == 'LMS-Helper/0.1'
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nDisallow: /' if denied else 'User-agent: *\nDisallow: /admin')
        source = 'dept' if request.url.host.startswith('cs.') else 'school'
        if request.url.path.endswith(('list.do', 'artclList.do')):
            return httpx.Response(200, text=listing(source))
        return httpx.Response(200, text='<div class="view-con">본문<script>secret()</script></div>')
    client = httpx.Client(transport=httpx.MockTransport(respond))
    crawler = NoticeCrawler(tmp_path, client, now=lambda: clock[0], sleep=sleep)
    return crawler, clock, requests, denied


@pytest.mark.parametrize('source', ['school', 'dept'])
def test_parsers(source):
    items, total = parse_list(listing(source), source)
    assert total == 1 and len(items) == 2
    assert items[0]['id'] == f'{source}-11' and items[0]['title'] == '공지 11'
    assert items[0]['posted_at'] == '2026-10-29T00:00:00+09:00'
    assert parse_body('<div class="view-con">본문<script>bad()</script></div>') == '본문'


def test_cache_pagination_and_spacing(setup):
    crawler, clock, requests, _ = setup
    first = crawler.listing('school', 'ru', 1, 1)
    assert first['has_more'] and first['items'][0]['id'] == 'school-12'
    count = len(requests)
    second = crawler.listing('school', 'en', 1, 2)
    assert not second['has_more'] and second['items'][0]['id'] == 'school-11'
    assert len(requests) == count
    assert all(b[1] - a[1] >= 1 for a, b in zip(requests, requests[1:]))
    clock[0] += 1800
    crawler.listing('school', 'ru', 10, 1)
    assert len(requests) == count + 2
    restarted = NoticeCrawler(crawler.root, crawler.client, now=lambda: clock[0])
    restarted.listing('school', 'ru', 10, 1)
    assert len(requests) == count + 2


def test_robots_denial_and_failed_attempt_throttled(setup):
    crawler, _, requests, denied = setup
    denied.append(True)
    for _ in range(2):
        with pytest.raises(NoticeError):
            crawler.listing('dept', 'ru', 10, 1)
    assert len(requests) == 1


def test_network_error(tmp_path, monkeypatch):
    def fail(request):
        raise httpx.ConnectError('offline', request=request)
    crawler = NoticeCrawler(tmp_path, httpx.Client(transport=httpx.MockTransport(fail)))
    monkeypatch.setattr(main, 'get_crawler', lambda: crawler)
    response = TestClient(main.app).get('/notices?source=school&lang=ru')
    assert response.status_code == 503 and isinstance(response.json()['detail'], str)


@pytest.mark.parametrize('query', ['source=course', 'source=dept&lang=xx', 'source=dept&limit=31',
                                  'source=dept&limit=0', 'source=dept&page=0'])
def test_api_validation(query):
    assert TestClient(main.app).get('/notices?' + query).status_code == 422


def test_notice_api(setup, monkeypatch):
    crawler, *_ = setup
    monkeypatch.setattr(main, 'get_crawler', lambda: crawler)
    response = TestClient(main.app).get('/notices?source=dept&lang=ru&limit=1&page=1')
    assert response.status_code == 200
    notice = response.json()['items'][0]
    assert set(notice) == {'id', 'source', 'course', 'title', 'title_tr', 'summary_tr', 'body_tr',
                           'deadline', 'deadline_text', 'action_tr', 'posted_at', 'url'}
    assert notice['deadline'] is None and notice['body_tr'] == ''


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
    response = crawler.listing('school', 'ru', 30, 1)
    assert [n['id'] for n in response['items']] == ['school-12', 'school-11', 'school-10']
    assert sum(url.path.endswith('view.do') for url in requests) == 3


def test_interrupted_refresh_is_not_reported_as_empty_success(tmp_path):
    crawler = NoticeCrawler(tmp_path, httpx.Client())
    crawler._write('school', {'attempted_at': crawler.now(), 'error': 'in progress'})
    with pytest.raises(NoticeError):
        crawler.listing('school', 'ru', 10, 1)


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
