import copy
import gzip
import json
from pathlib import Path
import sqlite3

import pytest

from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult


@pytest.fixture
def result():
    return ExtractionResult(
        url="https://movingtospain.com/example/",
        canonical_url="https://movingtospain.com/example/",
        title="Source guide",
        published_at="2026-01-01T00:00:00Z",
        metadata={"authors": ["Private Author"], "reviewers": ["Private Reviewer"], "categories": ["Immigration"]},
        original_text="Private Author\nOriginal author quote\nUseful requirements.",
        blocks=[
            {"key": "b1", "position": 0, "source_order": 3, "kind": "heading", "text": "Requirements",
             "heading_level": 2, "section_path": ["Requirements"], "data": {}, "flags": []},
            {"key": "b2", "position": 1, "source_order": 5, "kind": "paragraph", "text": "Useful requirements.",
             "section_path": ["Requirements"], "data": {}, "flags": []},
            {"key": "b3", "position": 2, "source_order": 8, "kind": "table", "text": "Item | Amount\nFee | €20",
             "section_path": ["Requirements"], "data": {"rows": [["Item", "Amount"], ["Fee", "€20"]], "header_rows": [0]}, "flags": []},
        ],
        links=[
            {"block_key": "b2", "source_order": 5, "url": "https://www.boe.es/example", "anchor": "official law", "kind": "external_reference", "disposition": "retained"},
            {"block_key": "b2", "source_order": 5, "url": "https://movingtospain.com/other/", "anchor": "other guide", "kind": "internal_reference", "disposition": "retained"},
            {"block_key": None, "source_order": 6, "url": "https://example.com/affiliate", "anchor": "Private affiliate", "kind": "external_reference", "disposition": "removed"},
        ],
        events=[
            {"source_order": 4, "reason": "attributed_quote", "text": "Original author quote",
             "selector": "blockquote", "requires_review": True, "prev_block_key": "b1", "next_block_key": "b2",
             "details": {"author": "Private Author", "text": "Another private excerpt"}},
        ],
        quality_flags=["content_gap"],
    )


def test_identical_extraction_is_idempotent_but_fetches_and_snapshots_are_preserved(tmp_path, result):
    with Database(tmp_path / "source_articles.db") as db:
        run = db.start_run("scrape", {"limit": 1})
        first = db.store_article(result, raw_html="<main>v1</main>", run_id=run)
        repeated = db.store_article(result, raw_html="<main>v1</main><!--new template-->", run_id=run)
        db.finish_run(run, {"saved": 1, "unchanged": 1})
        assert first["article_id"] == repeated["article_id"]
        assert first["version_id"] == repeated["version_id"]
        assert first["created_version"] is True
        assert repeated["created_version"] is False
        assert db.stats()["articles"] == 1
        assert db.stats()["versions"] == 1
        assert db.stats()["fetches"] == 2
        assert db.stats()["blocks"] == 3
        snapshots = db.connection.execute("SELECT raw_sha256, raw_path FROM source_fetches ORDER BY id").fetchall()
        assert snapshots[0]["raw_sha256"] != snapshots[1]["raw_sha256"]
        assert gzip.decompress((tmp_path / snapshots[0]["raw_path"]).read_bytes()) == b"<main>v1</main>"
        assert db.connection.execute("SELECT status FROM crawl_runs").fetchone()[0] == "completed"


def test_changed_content_and_rules_create_versions_without_overwriting_history(tmp_path, result):
    with Database(tmp_path / "articles.db") as db:
        first = db.store_article(result, raw_html="first")
        result.blocks[1]["text"] = "Changed requirement."
        second = db.store_article(result, raw_html="second")
        result.cleaner_version = "2.0.0"
        third = db.store_article(result, raw_html="second")
        assert len({first["version_id"], second["version_id"], third["version_id"]}) == 3
        assert db.list_articles()[0]["current_version_id"] == third["version_id"]
        old = db.connection.execute("SELECT clean_text FROM article_versions WHERE id=?", (first["version_id"],)).fetchone()[0]
        assert "Useful requirements." in old
        assert "Changed requirement." not in old
        assert db.get_export(article_id=first["article_id"])["article"]["cleaner_version"] == "2.0.0"


def test_reverting_to_an_existing_version_reuses_it(tmp_path, result):
    with Database(tmp_path / "articles.db") as db:
        original = copy.deepcopy(result)
        first = db.store_article(result, raw_html="first")
        result.events[0]["text"] = "Updated excluded quote"
        second = db.store_article(result, raw_html="second")
        reverted = db.store_article(original, raw_html="first")
        assert second["created_version"]
        assert reverted["version_id"] == first["version_id"]
        assert reverted["created_version"] is False
        assert db.stats()["versions"] == 2


def test_export_separates_source_audit_and_metadata_from_ai_text(tmp_path, result):
    with Database(tmp_path / "articles.db") as db:
        saved = db.store_article(result, raw_html="<html>Private raw source</html>")
        export = db.get_export(url=result.url)
        serialized = json.dumps(export)
        assert "Private Author" not in serialized
        assert "Private Reviewer" not in serialized
        assert "Original author quote" not in serialized
        assert "Another private excerpt" not in serialized
        assert "Private raw source" not in serialized
        assert "Private affiliate" not in serialized
        assert "https://movingtospain.com/other/" not in serialized
        assert export["quality"]["review_required"] is True
        assert export["content"]["clean_text"] == result.clean_text
        assert [block["id"] for block in export["content"]["blocks"]] == ["b1", "b2", "b3"]
        assert export["content"]["blocks"][2]["data"]["rows"][1] == ["Fee", "€20"]
        assert export["references"] == [{"block_id": "b2", "url": "https://www.boe.es/example", "anchor": "official law"}]
        assert export["gaps"][0]["previous_block_id"] == "b1"
        assert export["gaps"][0]["next_block_id"] == "b2"
        assert export["article"]["id"] == saved["article_id"]
        assert db.connection.execute("SELECT original_text FROM extraction_events").fetchone()[0] == "Original author quote"


def test_failed_child_insert_rolls_back_entire_article_and_fetch(tmp_path, result):
    result.blocks[1]["key"] = "b1"
    with Database(tmp_path / "articles.db") as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.store_article(result, raw_html="snapshot may remain")
        counts = db.stats()
        for key in ("sites", "articles", "versions", "blocks", "links", "events", "fetches"):
            assert counts[key] == 0
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_new_version_does_not_replace_last_good_version(tmp_path, result):
    with Database(tmp_path / "articles.db") as db:
        first = db.store_article(result, raw_html="good")
        result.blocks[1]["position"] = 0
        with pytest.raises(sqlite3.IntegrityError):
            db.store_article(result, raw_html="bad")
        assert db.list_articles()[0]["current_version_id"] == first["version_id"]
        assert db.stats()["versions"] == 1
        assert db.stats()["fetches"] == 1


def test_foreign_keys_reject_orphans_and_mismatched_current_version(tmp_path, result):
    with Database(tmp_path / "articles.db") as db:
        first = db.store_article(result, raw_html="first")
        result.url = result.canonical_url = "https://movingtospain.com/second/"
        second = db.store_article(result, raw_html="second")
        with pytest.raises(sqlite3.IntegrityError):
            db.record_fetch(None, result.url, article_id=999)
        with pytest.raises(sqlite3.IntegrityError):
            db.record_fetch(None, result.url, article_id=first["article_id"], version_id=second["version_id"])
        with pytest.raises(sqlite3.IntegrityError):
            with db.connection:
                db.connection.execute("UPDATE source_articles SET current_version_id=? WHERE id=?", (second["version_id"], first["article_id"]))
        assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_alias_urls_resolve_to_canonical_article_and_errors_do_not_create_articles(tmp_path, result):
    alias = "https://movingtospain.com/old-slug/"
    with Database(tmp_path / "articles.db") as db:
        saved = db.store_article(result, raw_html="source", requested_url=alias)
        assert db.has_article(alias)
        assert db.has_article(result.canonical_url)
        assert db.get_export(url=alias)["article"]["id"] == saved["article_id"]
        db.record_fetch(None, "https://movingtospain.com/missing/", status_code=404, error="Not found")
        assert not db.has_article("https://movingtospain.com/missing/")
        assert db.stats()["fetch_errors"] == 1
        assert db.stats()["articles"] == 1


def test_database_reopens_with_same_data_and_version(tmp_path, result):
    path = tmp_path / "nested" / "articles.db"
    with Database(path) as db:
        db.store_article(result, raw_html="source")
    with Database(path) as db:
        assert db.stats()["schema_version"] == 1
        assert db.stats()["articles"] == 1
        assert db.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.get_export(url=result.url)["content"]["clean_text"] == result.clean_text


def test_empty_extraction_and_ambiguous_export_selection_are_rejected(tmp_path, result):
    with Database(tmp_path / "articles.db") as db:
        result.blocks = []
        with pytest.raises(ValueError, match="no title or clean content"):
            db.store_article(result, raw_html="empty")
        with pytest.raises(ValueError, match="exactly one"):
            db.get_export()
        with pytest.raises(KeyError, match="No stored article"):
            db.get_export(article_id=99)


def test_future_schema_version_is_not_silently_opened(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="newer than supported"):
        Database(path)


def test_snapshot_restores_exact_current_version_html_offline(tmp_path, result):
    source = "<article><h1>Espa\u00f1a</h1><p>\u20ac20</p></article>"
    with Database(tmp_path / "articles.db") as db:
        saved = db.store_article(result, raw_html=source)
        assert db.get_snapshot(saved["article_id"]) == {"url": result.url, "html": source}
        # The same semantic extraction can have a newer template snapshot. The
        # version still has an exact, reproducible original source of its own.
        db.store_article(result, raw_html=source + "<!-- template change -->")
        assert db.get_snapshot(saved["article_id"])["html"] == source
        assert db.stats()["fetches"] == 2
        result.blocks[1]["text"] = "New revision"
        db.store_article(result, raw_html="<article>New revision</article>")
        assert db.get_snapshot(saved["article_id"])["html"] == "<article>New revision</article>"


@pytest.mark.parametrize("damage", ["missing", "gzip", "hash"])
def test_snapshot_damage_does_not_change_stored_current_version(tmp_path, result, damage):
    with Database(tmp_path / "articles.db") as db:
        saved = db.store_article(result, raw_html="<article>Good original</article>")
        row = db.connection.execute("SELECT raw_path FROM article_versions WHERE id=?", (saved["version_id"],)).fetchone()
        snapshot = tmp_path / row["raw_path"]
        if damage == "missing":
            snapshot.unlink()
            expected_error = FileNotFoundError
        elif damage == "gzip":
            snapshot.write_bytes(b"not gzip")
            expected_error = OSError
        else:
            snapshot.write_bytes(gzip.compress(b"<article>Changed without metadata</article>"))
            expected_error = ValueError
        with pytest.raises(expected_error):
            db.get_snapshot(saved["article_id"])
        assert db.list_articles()[0]["current_version_id"] == saved["version_id"]
        assert db.get_export(article_id=saved["article_id"])["content"]["clean_text"] == result.clean_text
        assert db.stats()["versions"] == 1
        assert db.stats()["fetches"] == 1
