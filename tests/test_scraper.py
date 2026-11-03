from collections import deque
import unittest

import requests

from src.extraction.models import ExtractionResult
from src.extraction.scraper import (
    HtmlResponse, HttpClient, ScrapeError, discover_guides, normalize_url, scrape_articles,
)


BASE = "https://movingtospain.com"
DOCUMENT = "<!doctype html><html><head><title>Guía</title></head><body>España €</body></html>"


class FakeResponse:
    def __init__(self, body=DOCUMENT, status=200, headers=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=UTF-8"}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class FakeSession:
    def __init__(self, responses, clock):
        self.responses = deque(responses)
        self.calls = []
        self.headers = {}
        self.clock = clock

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, self.clock.now))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def http(responses, **kwargs):
    clock = FakeClock()
    session = FakeSession(responses, clock)
    client = HttpClient(session=session, sleep=clock.sleep, clock=clock.clock, **kwargs)
    return client, session, clock


class IndexClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return HtmlResponse(url, value)


class FakeDatabase:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.saved, self.errors, self.finished = [], [], None

    def start_run(self, kind, config):
        self.config = config
        return 7

    def has_article(self, url):
        return url in self.existing

    def store_article(self, result, **kwargs):
        self.saved.append((result, kwargs))
        return {"article_id": 1, "version_id": 1}

    def record_fetch(self, run_id, url, **kwargs):
        self.errors.append((run_id, url, kwargs))

    def finish_run(self, run_id, stats, status):
        self.finished = (run_id, stats, status)


def extracted(html, url):
    return ExtractionResult(url=url, canonical_url=url, title="Example", blocks=[{"text": "Useful text"}])


def index(*slugs, next_page=None, extra=""):
    cards = "".join(f'<h2 class="entry-title"><a href="/{slug}/">{slug}</a></h2>' for slug in slugs)
    next_link = f'<a class="next page-numbers" href="/guides/page/{next_page}/">Next</a>' if next_page else ""
    return f"<html><body>{cards}{next_link}{extra}</body></html>"


class HttpClientTests(unittest.TestCase):
    def test_utf8_without_header_charset_and_real_spacing_between_requests(self):
        client, session, clock = http([FakeResponse(headers={"Content-Type": "text/html"}), FakeResponse()], delay=1.5)
        self.assertIn("España €", client.get(BASE + "/one/").html)
        client.get(BASE + "/two/")
        self.assertEqual([call[2] for call in session.calls], [0, 1.5])
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertEqual(clock.waits, [1.5])

    def test_honors_explicit_non_utf8_encoding(self):
        doc = DOCUMENT.replace("€", "EUR").encode("iso-8859-1")
        client, _, _ = http([FakeResponse(doc, headers={"Content-Type": "text/html; charset=iso-8859-1"})])
        self.assertIn("España", client.get(BASE).html)

    def test_retries_429_and_timeout_with_bounded_waits(self):
        client, session, clock = http([
            FakeResponse(status=429, headers={"Retry-After": "2"}), requests.Timeout("slow"), FakeResponse()
        ], delay=1, max_retries=2)
        self.assertEqual(client.get(BASE).status_code, 200)
        self.assertEqual([call[2] for call in session.calls], [0, 2, 4])
        self.assertTrue(all(value <= 30 for value in clock.waits))

    def test_stops_when_retry_limit_is_exhausted(self):
        client, session, _ = http([FakeResponse(status=503)] * 3, max_retries=2, delay=0)
        with self.assertRaises(ScrapeError) as result:
            client.get(BASE)
        self.assertEqual(result.exception.status_code, 503)
        self.assertEqual(len(session.calls), 3)

    def test_external_redirect_rejected_before_contacting_other_host(self):
        client, session, _ = http([FakeResponse(status=302, headers={"Location": "https://example.com/x"})])
        with self.assertRaisesRegex(ScrapeError, "fuera del sitio"):
            client.get(BASE)
        self.assertEqual(len(session.calls), 1)

    def test_internal_redirect_is_paced_and_final_url_returned(self):
        client, session, _ = http([FakeResponse(status=301, headers={"Location": "/new/"}), FakeResponse()], delay=2)
        response = client.get(BASE + "/old/")
        self.assertEqual(response.url, BASE + "/new/")
        self.assertEqual(session.calls[1][2], 2)

    def test_looping_redirect_is_error(self):
        client, session, _ = http([FakeResponse(status=302, headers={"Location": "/old/"})])
        with self.assertRaisesRegex(ScrapeError, "Bucle"):
            client.get(BASE + "/old/")
        self.assertEqual(len(session.calls), 1)

    def test_non_html_and_success_status_challenge_never_become_articles(self):
        for response in [FakeResponse(headers={"Content-Type": "image/png"}),
                         FakeResponse("<html><title>Security Verification</title><body>Check</body></html>")]:
            with self.subTest(response=response):
                client, _, _ = http([response])
                with self.assertRaises(ScrapeError):
                    client.get(BASE)
                self.assertTrue(response.closed)

    def test_html_size_limit_is_enforced(self):
        client, _, _ = http([FakeResponse()], max_bytes=10)
        with self.assertRaisesRegex(ScrapeError, "límite"):
            client.get(BASE)


class DiscoveryTests(unittest.TestCase):
    def test_discovers_only_cards_and_pages_deduplicates_and_exhausts(self):
        pages = {
            BASE + "/guides/": index("one", "two", next_page=2, extra='<a href="/services/">Service</a>'),
            BASE + "/guides/page/2/": index("two", "three", next_page=3),
            BASE + "/guides/page/3/": index("four"),
        }
        client = IndexClient(pages)
        self.assertEqual(discover_guides(max_pages=None, client=client), [BASE + f"/{s}/" for s in ("one", "two", "three", "four")])
        self.assertEqual(len(client.calls), 3)

    def test_page_limit_is_explicit(self):
        client = IndexClient({BASE + "/guides/": index("one", next_page=2)})
        self.assertEqual(discover_guides(max_pages=1, client=client), [BASE + "/one/"])
        self.assertEqual(len(client.calls), 1)

    def test_missing_selector_is_not_silent_exhaustion(self):
        client = IndexClient({BASE + "/guides/": "<html><h1>Blocked</h1></html>"})
        with self.assertRaisesRegex(ScrapeError, "tarjetas"):
            discover_guides(client=client)

    def test_numbered_pagination_fallback(self):
        client = IndexClient({BASE + "/guides/": index("one", extra='<a class="page-numbers" href="/guides/page/2/">2</a>'),
                              BASE + "/guides/page/2/": index("two")})
        self.assertEqual(len(discover_guides(client=client)), 2)

    def test_mailchimp_tracking_on_live_pagination_is_not_a_different_resource(self):
        client = IndexClient({
            BASE + "/guides/": index("one", extra='<a class="next page-numbers" href="/guides/page/2/?mc_cid=2h6c4i&amp;mc_eid=reader">Next</a>'),
            BASE + "/guides/page/2/": index("two"),
        })
        self.assertEqual(discover_guides(max_pages=None, client=client), [BASE + "/one/", BASE + "/two/"])
        self.assertEqual(client.calls[-1], BASE + "/guides/page/2/")

    def test_pagination_cannot_follow_an_article(self):
        client = IndexClient({BASE + "/guides/": index("one", extra='<a class="next page-numbers" href="/one/">Next</a>')})
        with self.assertRaisesRegex(ScrapeError, "índice"):
            discover_guides(client=client)


class ScrapeTests(unittest.TestCase):
    def test_failures_are_isolated_and_existing_and_duplicate_urls_skipped(self):
        db = FakeDatabase(existing=[BASE + "/existing/"])
        client = IndexClient({BASE + "/bad/": ScrapeError("HTTP 403", status_code=403), BASE + "/good/": DOCUMENT})
        stats = scrape_articles([BASE + "/existing/", BASE + "/bad/", BASE + "/good/", BASE + "/good/?utm_source=x"],
                                db, client=client, extractor=extracted)
        self.assertEqual((stats["success"], stats["skipped"], stats["failed"]), (1, 2, 1))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(db.errors), 1)
        self.assertEqual(db.errors[0][2]["status_code"], 403)
        self.assertEqual(db.saved[0][1]["run_id"], 7)
        self.assertEqual(db.finished[2], "partial")

    def test_refresh_fetches_existing_article(self):
        url = BASE + "/existing/"
        db, client = FakeDatabase(existing=[url]), IndexClient({url: DOCUMENT})
        stats = scrape_articles([url], db, refresh=True, client=client, extractor=extracted)
        self.assertEqual(stats["success"], 1)
        self.assertEqual(db.finished[2], "completed")

    def test_empty_extraction_and_unsupported_canonical_are_not_saved(self):
        url = BASE + "/empty/"
        for result in [ExtractionResult(url=url, canonical_url=url, title="Empty"),
                       ExtractionResult(url=url, canonical_url="https://example.com/", title="Other", blocks=[{"text": "text"}])]:
            with self.subTest(result=result):
                db = FakeDatabase()
                stats = scrape_articles([url], db, client=IndexClient({url: DOCUMENT}), extractor=lambda *_: result)
                self.assertEqual(stats["failed"], 1)
                self.assertFalse(db.saved)
                self.assertEqual(db.finished[2], "failed")

    def test_canonical_normalization_removes_tracking_but_not_meaningful_queries(self):
        self.assertEqual(normalize_url("http://www.movingtospain.com/guide?utm_source=x#heading"), BASE + "/guide/")
        self.assertEqual(normalize_url("/guide/?page=2&fbclid=x"), BASE + "/guide/?page=2")
        for url in ["https://example.com/x", "file:///etc/test", "https://movingtospain.com:1234/", "https://movingtospain.com@evil.test/"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)


if __name__ == "__main__":
    unittest.main()
