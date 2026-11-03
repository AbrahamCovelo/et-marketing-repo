"""Offline operation tests using real SQLite and the real cleaner."""
from collections import deque
import json
import threading

import pytest

from src.dashboard.jobs import JobBusyError, JobManager
from src.extraction.cleaner import extract_html
from src.extraction.db_manager import Database
from src.extraction.scraper import HtmlResponse, ScrapeError


BASE = "https://movingtospain.com/"


def html(slug="one", *, published="2026-11-03T22:00:00+01:00", modified="2026-11-03T23:30:00-05:00", text="Prepare the required documents and retain a copy of each one."):
    dates = "".join(f'<meta property="article:{name}_time" content="{value}">' for name, value in (("published", published), ("modified", modified)) if value is not None)
    return f'''<!doctype html><html lang="en"><head><title>A guide</title>{dates}
    <link rel="canonical" href="{BASE}{slug}/"></head><body><h1>A guide</h1>
    <article class="single-entry"><div class="entry-content single-content"><h2>Application documents</h2>
    <p>{text} Applicants should check the published requirements before preparing the application.
    Keep the original documents together and check their validity dates with the relevant authority.</p>
    </div></article></body></html>'''


class Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.closed = False

    def get(self, url):
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, HtmlResponse) else HtmlResponse(url, value)

    def close(self):
        self.closed = True


def manager(tmp_path, responses=None, **kwargs):
    client = Client(responses or {})
    instance = JobManager(tmp_path / "articles.db", client_factory=lambda **_: client,
                          runner=lambda task: task(), **kwargs)
    return instance, client


def seed(path, slug="one", **kwargs):
    content = html(slug, **kwargs)
    with Database(path) as db:
        return db.store_article(extract_html(content, BASE + slug + "/"), raw_html=content)


def scrape_payload(*slugs, **kwargs):
    return {"action": "scrape", "scope": "urls", "urls": [BASE + slug + "/" for slug in slugs], **kwargs}


@pytest.mark.parametrize("payload", [
    None, [], {"action": []}, {"action": "erase"}, {"action": "discover", "unknown": True},
    {"action": "discover", "all_pages": "false"}, {"action": "discover", "max_pages": True},
    {"action": "discover", "delay": float("nan")}, {"action": "discover", "timeout": 0},
    {"action": "discover", "date_from": "2026-01-01"},
    scrape_payload("one", scope=[]), scrape_payload("one", mode={}),
    scrape_payload("one", date_field="captured_at"),
    scrape_payload("one", date_from="2026-2-01"),
    scrape_payload("one", date_from="2026-12-01", date_to="2026-01-01"),
    {"action": "scrape", "scope": "urls", "urls": ["https://elsewhere.test/article/"]},
    {"action": "scrape", "scope": "urls", "urls": ["file:///private/file"]},
    {"action": "scrape", "scope": "urls", "urls": ["article/"]},
    {"action": "scrape", "scope": "urls", "urls": [BASE + "?secret=1"]},
    {"action": "scrape", "scope": "urls", "urls": [BASE + "article/?do=write"]},
    {"action": "scrape", "scope": "urls", "urls": [BASE + "guides/"]},
    {"action": "scrape", "scope": "selected", "article_ids": [999]},
    {"action": "reclean", "scope": "selected", "article_ids": [True]},
    {"action": "reclean", "scope": "all", "date_from": "2026-01-01"},
])
def test_invalid_requests_never_start_network(tmp_path, payload):
    m, client = manager(tmp_path)
    with pytest.raises(ValueError):
        m.submit(payload)
    assert not client.calls
    assert m.list_jobs() == []


def test_new_mode_skips_existing_and_refresh_updates(tmp_path):
    m, client = manager(tmp_path, {BASE + "one/": html(text="The certificate validity period has changed. Retain the current version of the requirements.")})
    first = seed(m.db_path)
    skipped = m.submit(scrape_payload("one"))
    assert skipped["progress"]["skipped"] == 1
    assert skipped["progress"]["processed"] == 1
    assert not client.calls
    updated = m.submit(scrape_payload("one", mode="refresh"))
    assert updated["status"] == "completed"
    assert updated["progress"]["success"] == 1
    with Database(m.db_path) as db:
        assert db.list_articles()[0]["current_version_id"] != first["version_id"]
        assert db.stats()["articles"] == 1
        assert db.stats()["versions"] == 2


def test_new_redirect_alias_is_remembered_without_new_version(tmp_path):
    m, client = manager(tmp_path, {BASE + "old-address/": HtmlResponse(BASE + "one/", html())})
    seed(m.db_path)
    alias = m.submit(scrape_payload("old-address"))
    assert alias["progress"]["skipped"] == 1
    assert client.calls == [BASE + "old-address/"]
    with Database(m.db_path) as db:
        assert db.has_article(BASE + "old-address/")
        assert db.stats()["versions"] == 1
    again = m.submit(scrape_payload("old-address"))
    assert again["progress"]["skipped"] == 1
    assert client.calls == [BASE + "old-address/"]


def test_modified_range_uses_current_source_not_stale_database(tmp_path):
    m, client = manager(tmp_path, {BASE + "one/": html(modified="2026-11-03T23:30:00-05:00")})
    old = seed(m.db_path, modified="2020-01-01T00:00:00Z")
    result = m.submit(scrape_payload("one", mode="refresh", date_field="modified_at",
                                    date_from="2026-11-03", date_to="2026-11-03"))
    assert result["progress"]["success"] == 1
    assert client.calls == [BASE + "one/"]
    with Database(m.db_path) as db:
        current = db.list_articles()[0]
        assert current["current_version_id"] != old["version_id"]
        assert current["modified_at"].startswith("2026-11-03")


def test_date_skips_preserve_existing_version_and_do_not_substitute_publication(tmp_path):
    m, client = manager(tmp_path, {
        BASE + "one/": html(modified="2026-11-02T23:59:59Z"),
        BASE + "two/": html("two", modified=None, published="2026-11-03T12:00:00Z"),
    })
    old = seed(m.db_path, modified="2020-01-01T00:00:00Z")
    job = m.submit(scrape_payload("one", "two", mode="refresh", date_field="modified_at",
                                 date_from="2026-11-03", date_to="2026-11-03"))
    assert job["status"] == "completed"
    assert job["progress"] == {"total": 2, "processed": 2, "success": 0, "skipped": 2,
                               "failed": 0, "out_of_range": 1, "missing_date": 1}
    with Database(m.db_path) as db:
        assert db.list_articles()[0]["current_version_id"] == old["version_id"]
        assert db.stats()["articles"] == 1
        assert db.stats()["versions"] == 1
        rows = db.connection.execute("SELECT details_json FROM source_fetches WHERE run_id=? ORDER BY id", (job["run_id"],)).fetchall()
        assert [json.loads(row[0])["disposition"] for row in rows] == ["outside_date_range", "missing_date"]


def test_failures_are_isolated_and_persisted(tmp_path):
    m, client = manager(tmp_path, {BASE + "bad/": ScrapeError("HTTP 403", status_code=403),
                                 BASE + "one/": html()})
    job = m.submit(scrape_payload("bad", "one"))
    assert job["status"] == "partial"
    assert job["progress"]["failed"] == job["progress"]["success"] == 1
    assert job["progress"]["processed"] == 2
    assert client.closed
    with Database(m.db_path) as db:
        assert db.stats()["articles"] == 1
        assert db.connection.execute("SELECT status FROM crawl_runs WHERE id=?", (job["run_id"],)).fetchone()[0] == "partial"
        assert db.connection.execute("SELECT status_code FROM source_fetches WHERE error IS NOT NULL").fetchone()[0] == 403


def test_discover_stores_only_index_history_and_reports_urls(tmp_path):
    page = '<html><body><h2 class="entry-title"><a href="/one/">One</a></h2><h2 class="entry-title"><a href="/two/">Two</a></h2></body></html>'
    m, client = manager(tmp_path, {BASE + "guides/": page})
    job = m.submit({"action": "discover", "limit": 1})
    assert job["status"] == "completed"
    assert job["result"]["urls"] == [BASE + "one/"]
    assert job["progress"]["success"] == 0
    assert client.calls == [BASE + "guides/"]
    with Database(m.db_path) as db:
        assert db.stats()["articles"] == 0
        assert db.stats()["fetches"] == 1


def test_index_failure_finishes_run_and_releases_active_job(tmp_path):
    m, client = manager(tmp_path, {BASE + "guides/": ScrapeError("No index", status_code=503)})
    job = m.submit({"action": "discover"})
    assert job["status"] == "failed"
    assert job["progress"]["failed"] == 1
    assert client.closed
    with Database(m.db_path) as db:
        assert db.connection.execute("SELECT status FROM crawl_runs WHERE id=?", (job["run_id"],)).fetchone()[0] == "failed"
    # A failed job must not indefinitely lock the panel.
    assert m.submit({"action": "discover"})["status"] == "failed"


def test_catalog_scrape_discovers_and_applies_limit(tmp_path):
    page = '<html><body><h2 class="entry-title"><a href="/one/">One</a></h2><h2 class="entry-title"><a href="/two/">Two</a></h2></body></html>'
    m, client = manager(tmp_path, {BASE + "guides/": page, BASE + "one/": html()})
    job = m.submit({"action": "scrape", "scope": "catalog", "limit": 1})
    assert job["progress"]["success"] == 1
    assert client.calls == [BASE + "guides/", BASE + "one/"]


def test_reclean_uses_no_client_and_keeps_idempotent_versions(tmp_path):
    m, client = manager(tmp_path)
    first = seed(m.db_path)
    m.client_factory = lambda **_: pytest.fail("Local reclean must not initialize HTTP")
    job = m.submit({"action": "reclean", "scope": "selected", "article_ids": [first["article_id"]]})
    assert job["status"] == "completed"
    assert job["progress"]["success"] == 1
    with Database(m.db_path) as db:
        assert db.stats()["versions"] == 1
        assert db.connection.execute("SELECT status_code FROM source_fetches WHERE run_id=?", (job["run_id"],)).fetchone()[0] is None


def test_corrupt_reclean_snapshot_does_not_replace_good_content(tmp_path):
    m, _ = manager(tmp_path)
    first = seed(m.db_path)
    with Database(m.db_path) as db:
        snapshot = db.connection.execute("SELECT raw_path FROM article_versions").fetchone()[0]
    (tmp_path / snapshot).write_bytes(b"corrupt")
    job = m.submit({"action": "reclean", "scope": "all"})
    assert job["status"] == "failed"
    assert job["progress"]["failed"] == 1
    with Database(m.db_path) as db:
        assert db.list_articles()[0]["current_version_id"] == first["version_id"]


def test_concurrent_jobs_are_rejected_and_return_values_are_copies(tmp_path):
    tasks = deque()
    m = JobManager(tmp_path / "articles.db", runner=tasks.append, max_jobs=2)
    first = m.submit({"action": "reclean", "scope": "all"})
    assert first["status"] == "queued"
    first["progress"]["failed"] = 999
    m.list_jobs()[0]["status"] = "tampered"
    assert m.get_job(first["id"])["progress"]["failed"] == 0
    with pytest.raises(JobBusyError):
        m.submit({"action": "reclean", "scope": "all"})
    tasks.popleft()()
    assert m.get_job(first["id"])["status"] == "completed"
    second = m.submit({"action": "reclean", "scope": "all"})
    tasks.popleft()()
    third = m.submit({"action": "reclean", "scope": "all"})
    tasks.popleft()()
    assert [job["id"] for job in m.list_jobs()] == [third["id"], second["id"]]
    with pytest.raises(KeyError):
        m.get_job(first["id"])


def test_default_runner_is_asynchronous_and_releases_after_completion(tmp_path):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    client = Client({BASE + "one/": html()})

    def extractor(body, url):
        entered.set()
        assert release.wait(3)
        return extract_html(body, url)

    m = JobManager(tmp_path / "articles.db", client_factory=lambda **_: client, extractor=extractor)
    original_update = m._update

    def update(job_id, **values):
        original_update(job_id, **values)
        if values.get("stage") == "finished":
            finished.set()

    m._update = update
    job = m.submit(scrape_payload("one"))
    try:
        assert entered.wait(3)
        assert m.get_job(job["id"])["status"] == "running"
        with pytest.raises(JobBusyError):
            m.submit(scrape_payload("one"))
    finally:
        release.set()
    assert finished.wait(3)
    assert m.get_job(job["id"])["status"] == "completed"
