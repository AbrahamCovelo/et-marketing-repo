"""Dashboard queries must preserve source history and filter real calendar days."""
from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from src.dashboard.repository import Repository, date_in_range, source_calendar_date, validate_date_range
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult


def article(slug, title, text, *, published="2026-03-10T23:30:00+01:00", modified="2026-03-12T05:00:00Z", review=False):
    url = f"https://movingtospain.com/{slug}/"
    return ExtractionResult(
        url=url, canonical_url=url, title=title, published_at=published, modified_at=modified,
        metadata={"authors": ["Source Author"], "categories": ["immigration"]},
        original_text=f"{text}\nRemoved source quote.",
        blocks=[{"key": "b1", "position": 0, "source_order": 1, "kind": "paragraph",
                 "text": text, "section_path": ["Documents"], "data": {"source_section_ids": ["s1"]}, "flags": []}],
        links=[{"block_key": "b1", "source_order": 1, "url": "https://www.boe.es/example",
                "anchor": "Source", "kind": "external_reference", "disposition": "retained"}],
        events=[{"source_order": 2, "reason": "editorial_quote", "text": "Removed source quote.",
                 "selector": "blockquote", "requires_review": review,
                 "prev_block_key": "b1", "next_block_key": None, "details": {"action": "quarantined"}}],
        quality_flags=["needs_editorial_review"] if review else [],
    )


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "articles.db"
    items = [
        article("spain-visa", "España y visados", "Información de jubilación y trámites. A literal 100% fee and field_name.", review=True),
        article("housing", "Housing", "A distinct rental paragraph.", published="2026-03-11T00:01:00+14:00", modified="2026-03-13T01:00:00Z"),
        article("without-dates", "Without dates", "No date has been supplied by the source.", published=None, modified=None),
    ]
    with Database(path) as db:
        for index, item in enumerate(items):
            db.store_article(item, raw_html="<html>source</html>", fetched_at=f"2026-04-0{index + 1}T02:00:00Z")
    return path, Repository(path), items


def test_search_is_case_and_accent_insensitive_literal_and_injection_safe(corpus):
    _, repo, _ = corpus
    assert [row["id"] for row in repo.list_articles(q="ESPANA")["items"]] == [1]
    assert repo.list_articles(q="JUBILACION")["total"] == 1
    assert repo.list_articles(q="%")['total'] == 1
    assert repo.list_articles(q="_")['total'] == 1
    assert repo.list_articles(q="' OR 1=1 --")['total'] == 0
    assert repo.list_articles(q="without-dates")['items'][0]['id'] == 3
    assert repo.summary()["articles"] == 3


def test_publication_filters_use_source_calendar_date_and_exclude_missing(corpus):
    _, repo, _ = corpus
    result = repo.list_articles(date_from="2026-03-11", date_to="2026-03-11")
    assert [row["id"] for row in result["items"]] == [2]
    assert repo.list_articles(date_from="2026-03-10", date_to="2026-03-11")["total"] == 2
    assert repo.list_articles(date_to="2026-03-10")["items"][0]["id"] == 1
    assert repo.list_articles()["total"] == 3
    result = repo.list_articles(date_field="modified_at", date_from="2026-03-13", date_to="2026-03-13")
    assert [row["id"] for row in result["items"]] == [2]


def test_capture_date_filter_uses_bogota_day_not_utc(corpus):
    _, repo, _ = corpus
    # 1 April at 02:00 UTC is still 31 March in Bogotá.
    result = repo.list_articles(date_field="captured_at", date_from="2026-03-31", date_to="2026-03-31")
    assert [row["id"] for row in result["items"]] == [1]
    assert result["items"][0]["captured_at"] == "2026-04-01T02:00:00Z"
    assert source_calendar_date("2026-04-01T00:01:00+02:00", "published_at") == "2026-04-01"


@pytest.mark.parametrize("kwargs", [
    {"date_from": "2026-02-30"}, {"date_to": "03/11/2026"},
    {"date_from": "2026-11-03", "date_to": "2026-01-01"},
    {"date_field": "published_at) OR 1=1 --"}, {"page": 0}, {"page": True},
    {"page_size": 101}, {"page_size": "25"}, {"review": "approved"},
    {"selected_only": "true"}, {"sort": "title; DROP TABLE source_articles"}, {"q": "x" * 501},
])
def test_invalid_filters_fail_explicitly(corpus, kwargs):
    _, repo, _ = corpus
    with pytest.raises(ValueError):
        repo.list_articles(**kwargs)


def test_pagination_and_review_filters_are_consistent(corpus):
    _, repo, _ = corpus
    first = repo.list_articles(page_size=1, sort="title_asc")
    second = repo.list_articles(page=2, page_size=1, sort="title_asc")
    assert first["total"] == 3 and first["pages"] == 3
    assert first["items"][0]["id"] == 1
    assert second["items"][0]["id"] == 2
    assert repo.list_articles(page=4, page_size=1)["items"] == []
    assert repo.list_articles(review="required")["total"] == 1
    assert repo.list_articles(review="clear")["total"] == 2
    assert repo.list_articles(review="needs_review")["total"] == 1
    assert repo.list_articles(review="clean")["total"] == 2
    assert repo.list_articles(q="does not exist")["pages"] == 1


def test_selections_persist_and_invalid_ids_do_not_partially_apply(corpus):
    path, repo, _ = corpus
    assert repo.set_selection([1, 1, 2], True) == {"selected_count": 2}
    again = Repository(path)
    assert again.selected_ids() == [1, 2]
    assert again.list_articles(selected_only=True)["total"] == 2
    assert again.detail(1)["article"]["selected"] is True
    with pytest.raises(ValueError):
        again.set_selection([3, 999], True)
    assert again.selected_ids() == [1, 2]
    with pytest.raises(ValueError):
        again.set_selection([1, 999], False)
    assert again.selected_ids() == [1, 2]
    assert again.set_selection([2], False) == {"selected_count": 1}
    assert again.set_selection([], False) == {"selected_count": 1}
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM article_versions").fetchone()[0] == 3
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("ids, selected", [([True], True), ([0], True), (["1"], True), ([2**64], True), ("1", True), ([1], "true"), ([1] * 5001, True)])
def test_selection_validation(corpus, ids, selected):
    _, repo, _ = corpus
    with pytest.raises(ValueError):
        repo.set_selection(ids, selected)


def test_detail_has_current_content_dates_and_history_with_audit_separate(corpus):
    path, repo, items = corpus
    changed = deepcopy(items[0])
    changed.blocks[0]["text"] = "Updated useful text."
    changed.modified_at = "2026-03-20T09:00:00+01:00"
    with Database(path) as db:
        db.store_article(changed, raw_html="<html>new source</html>", fetched_at="2026-04-05T20:00:00Z")
    detail = repo.detail(1)
    assert detail["article"]["published_at"] == items[0].published_at
    assert detail["article"]["modified_at"] == changed.modified_at
    assert detail["article"]["captured_at"] == "2026-04-05T20:00:00Z"
    assert detail["article"]["version_count"] == 2
    assert detail["clean_text"] == "Updated useful text."
    assert "Removed source quote" not in detail["clean_text"]
    assert "Removed source quote" in detail["original_text"]
    assert detail["events"][0]["original_text"] == "Removed source quote."
    assert detail["events"][0]["requires_review"] is True
    assert detail["events"][0]["details"] == {"action": "quarantined"}
    assert detail["blocks"][0]["data"]["source_section_ids"] == ["s1"]
    assert detail["links"][0]["url"] == "https://www.boe.es/example"
    assert detail["article"]["metadata"]["authors"] == ["Source Author"]
    assert [v["current"] for v in detail["versions"]] == [True, False]
    assert detail["quality_flags"] == ["needs_editorial_review"]
    with pytest.raises(KeyError):
        repo.detail(999)


def test_summary_counts_current_versions_and_runs_decode_json(corpus):
    path, repo, items = corpus
    with Database(path) as db:
        updated = deepcopy(items[0])
        updated.blocks[0]["text"] = "Changed text."
        db.store_article(updated, raw_html="new")
        run = db.start_run("scrape", {"urls": [updated.url]})
        db.finish_run(run, {"success": 1, "failed": 0})
    summary = repo.summary()
    assert summary["articles"] == 3 and summary["versions"] == 4
    assert summary["review_required"] == 1
    assert summary["missing_publication_dates"] == summary["missing_modification_dates"] == 1
    assert summary["words"] == sum(len(item.clean_text.split()) for item in [updated, *items[1:]])
    assert summary["last_run_at"]
    run = repo.recent_runs()[0]
    assert run["config"]["urls"] == [updated.url]
    assert run["stats"]["success"] == 1
    assert run["status"] == "completed"


def test_date_helpers_validate_ranges_and_treat_unknown_dates_honestly():
    assert validate_date_range("", None) == (None, None)
    assert date_in_range(None) is True
    assert date_in_range(None, "2026-01-01") is False
    assert date_in_range("not a date", date_to="2026-01-01") is False
    assert date_in_range("2026-03-10T23:59:00-12:00", "2026-03-10", "2026-03-10") is True
    assert date_in_range("2026-03-11T01:00:00Z", "2026-03-10", "2026-03-10", date_field="captured_at") is True
    with pytest.raises(ValueError):
        validate_date_range("2026-2-1")
    with pytest.raises(ValueError):
        date_in_range(None, date_field="unsafe_column")


def test_empty_database_summary_and_selection(tmp_path):
    repo = Repository(tmp_path / "empty.db")
    assert repo.summary() == {
        "articles": 0, "review_required": 0, "words": 0, "versions": 0,
        "selected_count": 0, "missing_publication_dates": 0,
        "missing_modification_dates": 0, "last_run_at": None,
    }
    assert repo.list_articles()["items"] == []
    assert repo.selected_ids() == []
    assert repo.recent_runs() == []
