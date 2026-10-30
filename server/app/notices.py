"""Recent board snapshots. Run one server worker to share crawl throttling."""
import json
import os
import re
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from . import service
from .notice_extract import NoticeExtractionError, NoticeExtractor, get_extractor

USER_AGENT = "LMS-Helper/0.1"
REFRESH_SECONDS = 30 * 60
RETRY_SECONDS = 5 * 60
BOARDS = {
    "school": "https://www.inhatc.ac.kr/combBbs/kr/2/list.do",
    "dept": "https://cs.inhatc.ac.kr/bbs/cs/104/artclList.do",
}


class NoticeError(Exception):
    pass


def parse_list(html: str, source: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one(".bbs_type_table table, table")
    if table is None:
        raise NoticeError("공지 목록 구조를 확인할 수 없음")
    items = []
    recognized_links = 0
    for row in table.select("tbody tr"):
        link, date = row.select_one(".td-subject a"), row.select_one(".td-date")
        if link is None or date is None:
            continue
        if source == "school":
            match = re.search(r"jf_combBbs_view\('kr',\s*'2',\s*'(\d+)',\s*'(\d+)'\)", link.get("href", ""))
            if not match:
                continue
            board_id, article_id = match.groups()
            url = f"https://www.inhatc.ac.kr/combBbs/kr/2/{board_id}/{article_id}/view.do"
        else:
            match = re.fullmatch(r"/bbs/cs/104/(\d+)/artclView.do", link.get("href", ""))
            if not match:
                continue
            article_id = match[1]
            url = urljoin(BOARDS[source], match[0])
        recognized_links += 1
        raw_date = date.get_text(strip=True)
        match_date = re.match(r"^(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})(?:[.\sT]|$)", raw_date)
        if not match_date:
            continue
        try:
            posted = datetime(*map(int, match_date.groups())).strftime("%Y-%m-%dT00:00:00+09:00")
        except ValueError:
            continue
        title_node = link.select_one("strong") or link
        for badge in title_node.select(".new, .icon"):
            badge.decompose()
        items.append({"id": f"{source}-{article_id}", "source": source, "course": None,
                      "title": title_node.get_text(" ", strip=True), "posted_at": posted, "url": url})
    if not recognized_links and table.select_one(".td-subject"):
        raise NoticeError("공지 링크 구조를 확인할 수 없음")
    total = soup.select_one("._totPage")
    total_text = total.get_text(strip=True) if total else "1"
    return items, max(1, int(total_text)) if total_text.isdigit() else 1


def parse_body(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".view-con")
    if body is None:
        raise NoticeError("공지 본문 구조를 확인할 수 없음")
    for node in body.select("script, style"):
        node.decompose()
    return body.get_text("\n", strip=True)


class NoticeCrawler:
    def __init__(self, root: Path, client: httpx.Client, pages: int = 3,
                 now=time.time, sleep=time.sleep, extractor: NoticeExtractor | None = None) -> None:
        self.root, self.client, self.pages = root, client, pages
        self.now, self.sleep = now, sleep
        self.extractor = extractor if extractor is not None else get_extractor()
        self.source_locks = {source: Lock() for source in BOARDS}
        self.request_lock = Lock()
        self.worker_lock = Lock()
        self.last_request: float | None = None
        self.stopped = Event()
        self.worker: Thread | None = None
        root.mkdir(parents=True, exist_ok=True)

    def _read(self, name: str) -> dict:
        path = self.root / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _write(self, name: str, value: dict) -> None:
        path = self.root / f"{name}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def _request(self, url: str, robots: bool = False) -> str:
        with self.request_lock:
            if self.stopped.is_set():
                raise NoticeError("공지 수집 종료 중")
            if self.last_request is not None:
                self.sleep(max(0, 1 - (self.now() - self.last_request)))
            self.last_request = self.now()
            response = self.client.get(url, headers={"User-Agent": USER_AGENT})
            if robots and response.status_code == 404:
                return "User-agent: *\nAllow: /"
            response.raise_for_status()
            return response.text

    def _refresh(self, source: str) -> list[dict]:
        base = BOARDS[source]
        robots_url = urljoin(base, "/robots.txt")
        robots = RobotFileParser(robots_url)
        robots.parse(self._request(robots_url, robots=True).splitlines())

        def fetch(url: str) -> str:
            if urlsplit(url).netloc != urlsplit(base).netloc or not robots.can_fetch(USER_AGENT, url):
                raise NoticeError("robots.txt에서 공지 수집을 허용하지 않음")
            return self._request(url)

        collected = {}
        for page in range(1, self.pages + 1):
            rows, total = parse_list(fetch(f"{base}?page={page}"), source)
            for row in rows:
                cached = self._read(row["id"])
                if not cached:
                    cached = {**row, "body": parse_body(fetch(row["url"]))}
                    self._write(row["id"], cached)
                article = {**cached, **row}
                facts = self.extractor.extract(article["title"], article["body"])
                collected[row["id"]] = {**article, **facts}
            if page >= total:
                break
        return sorted(collected.values(), key=lambda n: (n["posted_at"], int(n["id"].split("-")[1])), reverse=True)

    def refresh(self, source: str) -> bool:
        lock = self.source_locks[source]
        if not lock.acquire(blocking=False):
            return False
        try:
            state = self._read(source)
            interval = RETRY_SECONDS if state.get("error") else REFRESH_SECONDS
            if "attempted_at" in state and self.now() - state["attempted_at"] < interval:
                return False
            state["attempted_at"] = self.now()
            state["error"] = "공지 수집이 아직 완료되지 않음. 잠시 후 다시 시도하세요."
            self._write(source, state)
            try:
                items = self._refresh(source)
            except (httpx.HTTPError, ValueError, OSError, NoticeError, NoticeExtractionError):
                state["error"] = "학교 공지를 가져올 수 없음. 잠시 후 다시 시도하세요."
                self._write(source, state)
                return False
            state["items"] = items
            state["succeeded_at"] = self.now()
            state.pop("error", None)
            self._write(source, state)
            return True
        finally:
            lock.release()

    def listing(self, source: str, limit: int, page: int) -> dict:
        state = self._read(source)
        if "items" not in state:
            raise NoticeError(state.get("error", "공지 수집 중입니다. 잠시 후 다시 시도하세요."))
        start = (page - 1) * limit
        fields = ("id", "source", "course", "title", "body", "summary", "deadline", "deadline_text",
                  "action", "posted_at", "url")
        items = [{key: n.get(key, "" if key in ("body", "summary") else None) for key in fields}
                 for n in state["items"][start:start + limit]]
        return {"items": items, "page": page, "has_more": start + limit < len(state["items"])}

    def _run(self) -> None:
        while not self.stopped.is_set():
            for source in BOARDS:
                if self.stopped.is_set():
                    return
                try:
                    self.refresh(source)
                except (OSError, ValueError):
                    # Keep the worker alive if the on-disk cache is unavailable.
                    pass
            self.stopped.wait(1)

    def start(self) -> None:
        with self.worker_lock:
            if self.worker is not None and self.worker.is_alive():
                return
            self.stopped.clear()
            self.worker = Thread(target=self._run, name="notice-crawler", daemon=True)
            self.worker.start()

    def stop(self) -> None:
        self.stopped.set()
        if self.worker is not None:
            self.worker.join(timeout=2)


@lru_cache(maxsize=1)
def get_crawler() -> NoticeCrawler:
    return NoticeCrawler(service.DATA_DIR / "notices", httpx.Client(timeout=30, follow_redirects=False),
                         pages=max(1, int(os.getenv("NOTICE_CRAWL_PAGES", "3"))))
