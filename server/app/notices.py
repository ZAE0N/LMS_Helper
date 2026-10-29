"""Recent board snapshots. Run one server worker to share crawl throttling."""
import json
import os
import re
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Lock
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from . import service

USER_AGENT = "LMS-Helper/0.1"
REFRESH_SECONDS = 30 * 60
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
        raw_date = date.get_text(strip=True)
        try:
            posted = datetime.strptime(raw_date, "%Y.%m.%d.").strftime("%Y-%m-%dT00:00:00+09:00")
        except ValueError as e:
            raise NoticeError("공지 작성일 형식 오류") from e
        title_node = link.select_one("strong") or link
        for badge in title_node.select(".new, .icon"):
            badge.decompose()
        items.append({"id": f"{source}-{article_id}", "source": source, "course": None,
                      "title": title_node.get_text(" ", strip=True), "posted_at": posted, "url": url})
    if not items and table.select_one(".td-subject"):
        raise NoticeError("공지 링크 구조를 확인할 수 없음")
    total = soup.select_one("._totPage")
    return items, int(total.get_text(strip=True)) if total else 1


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
                 now=time.time, sleep=time.sleep):
        self.root, self.client, self.pages = root, client, pages
        self.now, self.sleep = now, sleep
        self.lock = Lock()
        self.last_request: float | None = None
        root.mkdir(parents=True, exist_ok=True)

    def _read(self, name: str) -> dict:
        path = self.root / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _write(self, name: str, value: dict) -> None:
        path = self.root / f"{name}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def _request(self, url: str) -> str:
        if self.last_request is not None:
            self.sleep(max(0, 1 - (self.now() - self.last_request)))
        self.last_request = self.now()
        response = self.client.get(url, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response.text

    def _refresh(self, source: str) -> list[dict]:
        base = BOARDS[source]
        robots_url = urljoin(base, "/robots.txt")
        robots = RobotFileParser(robots_url)
        robots.parse(self._request(robots_url).splitlines())

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
                collected[row["id"]] = {**cached, **row}
            if page >= total:
                break
        return sorted(collected.values(), key=lambda n: (n["posted_at"], int(n["id"].split("-")[1])), reverse=True)

    def listing(self, source: str, lang: str, limit: int, page: int) -> dict:
        with self.lock:
            state = self._read(source)
            if not state or self.now() - state["attempted_at"] >= REFRESH_SECONDS:
                state["attempted_at"] = self.now()
                state["error"] = "공지 수집이 아직 완료되지 않음. 잠시 후 다시 시도하세요."
                self._write(source, state)
                try:
                    state["items"] = self._refresh(source)
                    state.pop("error", None)
                except (httpx.HTTPError, ValueError, NoticeError) as e:
                    state["error"] = "학교 공지를 가져올 수 없음. 잠시 후 다시 시도하세요."
                    self._write(source, state)
                    raise NoticeError(state["error"]) from e
                self._write(source, state)
            if state.get("error"):
                raise NoticeError(state["error"])
            start = (page - 1) * limit
            # Translation is deferred: keep the agreed Notice fields without passing Korean off as translated text.
            items = [{k: n[k] for k in ("id", "source", "course", "title", "posted_at", "url")}
                     | {"title_tr": "", "summary_tr": "", "body_tr": "", "deadline": None,
                        "deadline_text": None, "action_tr": None}
                     for n in state.get("items", [])[start:start + limit]]
            return {"items": items, "page": page, "has_more": start + limit < len(state.get("items", []))}


@lru_cache(maxsize=1)
def get_crawler() -> NoticeCrawler:
    return NoticeCrawler(service.DATA_DIR / "notices", httpx.Client(timeout=30, follow_redirects=False),
                         pages=max(1, int(os.getenv("NOTICE_CRAWL_PAGES", "3"))))
