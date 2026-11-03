"""SQLite staging store with immutable extraction versions and separate fetch history.

Raw snapshots contain only the downloaded HTML response, never images or media.
They are compressed and addressed by SHA-256 beside the database. The AI export
is intentionally a different view: it cannot expose removed prose or bylines.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .models import ExtractionResult


SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_sites (
    id INTEGER PRIMARY KEY,
    hostname TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_articles (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES source_sites(id),
    canonical_url TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL,
    language TEXT NOT NULL,
    current_version_id INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    FOREIGN KEY (current_version_id, id)
        REFERENCES article_versions(id, article_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS article_versions (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES source_articles(id),
    content_hash TEXT NOT NULL,
    cleaner_version TEXT NOT NULL,
    title TEXT NOT NULL,
    language TEXT NOT NULL,
    published_at TEXT,
    modified_at TEXT,
    captured_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    quality_flags_json TEXT NOT NULL CHECK (json_valid(quality_flags_json)),
    review_required INTEGER NOT NULL CHECK (review_required IN (0, 1)),
    original_text TEXT NOT NULL,
    clean_text TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    UNIQUE (article_id, content_hash),
    UNIQUE (id, article_id)
);
CREATE TABLE IF NOT EXISTS article_blocks (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES article_versions(id),
    block_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_order INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    heading_level INTEGER,
    section_path_json TEXT NOT NULL CHECK (json_valid(section_path_json)),
    data_json TEXT NOT NULL CHECK (json_valid(data_json)),
    flags_json TEXT NOT NULL CHECK (json_valid(flags_json)),
    UNIQUE (version_id, block_key),
    UNIQUE (version_id, position)
);
CREATE TABLE IF NOT EXISTS article_links (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES article_versions(id),
    block_key TEXT,
    source_order INTEGER,
    url TEXT NOT NULL,
    anchor TEXT NOT NULL,
    kind TEXT NOT NULL,
    disposition TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_events (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES article_versions(id),
    source_order INTEGER,
    reason TEXT NOT NULL,
    original_text TEXT NOT NULL,
    selector TEXT,
    requires_review INTEGER NOT NULL CHECK (requires_review IN (0, 1)),
    prev_block_key TEXT,
    next_block_key TEXT,
    details_json TEXT NOT NULL CHECK (json_valid(details_json))
);
CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    stats_json TEXT NOT NULL CHECK (json_valid(stats_json))
);
CREATE TABLE IF NOT EXISTS source_fetches (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES crawl_runs(id),
    requested_url TEXT NOT NULL,
    final_url TEXT,
    fetched_at TEXT NOT NULL,
    status_code INTEGER,
    error TEXT,
    article_id INTEGER REFERENCES source_articles(id),
    version_id INTEGER,
    raw_sha256 TEXT,
    raw_path TEXT,
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    CHECK (version_id IS NULL OR article_id IS NOT NULL),
    FOREIGN KEY (version_id, article_id) REFERENCES article_versions(id, article_id)
);
CREATE INDEX IF NOT EXISTS idx_versions_article ON article_versions(article_id);
CREATE INDEX IF NOT EXISTS idx_blocks_order ON article_blocks(version_id, position);
CREATE INDEX IF NOT EXISTS idx_links_version ON article_links(version_id);
CREATE INDEX IF NOT EXISTS idx_events_version ON extraction_events(version_id);
CREATE INDEX IF NOT EXISTS idx_fetches_run ON source_fetches(run_id);
CREATE INDEX IF NOT EXISTS idx_fetches_url ON source_fetches(requested_url);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(result: ExtractionResult) -> str:
    # Deliberately excludes fetch time, requested URL, and template-only HTML changes.
    # Includes excluded content and rules so a cleaning change is never hidden.
    semantic = {
        "canonical_url": result.canonical_url,
        "title": result.title,
        "language": result.language,
        "published_at": result.published_at,
        "modified_at": result.modified_at,
        "metadata": result.metadata,
        "blocks": result.blocks,
        "links": result.links,
        "events": result.events,
        "quality_flags": result.quality_flags,
        "original_text": result.original_text,
        "cleaner_version": result.cleaner_version,
    }
    return hashlib.sha256(_json(semantic).encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: str | Path = "data/source_articles.db", *, raw_dir: str | Path | None = None):
        self.path = Path(path).resolve() if str(path) != ":memory:" else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir = Path(raw_dir).resolve() if raw_dir else (
            self.path.parent / "raw" if self.path else Path("data/raw").resolve()
        )
        self.connection = sqlite3.connect(str(self.path) if self.path else ":memory:", timeout=30)
        self.conn = self.connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self.close()
            raise RuntimeError(f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}")
        if version == 0:
            try:
                self.connection.executescript("BEGIN;\n" + SCHEMA + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")
            except Exception:
                self.connection.rollback()
                self.close()
                raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def start_run(self, kind: str, config: dict[str, Any]) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO crawl_runs(kind, started_at, status, config_json, stats_json) VALUES (?, ?, 'running', ?, '{}')",
                (kind, _now(), _json(config)),
            )
        return cursor.lastrowid

    def finish_run(self, run_id: int, stats: dict[str, Any], status: str = "completed") -> None:
        if status not in {"completed", "partial", "failed"}:
            raise ValueError("A finished run must have status completed, partial, or failed")
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE crawl_runs SET finished_at=?, status=?, stats_json=? WHERE id=?",
                (_now(), status, _json(stats), run_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"Unknown crawl run: {run_id}")

    def _insert_fetch(self, run_id: int | None, url: str, status_code: int | None = None,
                      error: str | None = None, article_id: int | None = None,
                      version_id: int | None = None, **details: Any) -> int:
        cursor = self.connection.execute(
            """INSERT INTO source_fetches(run_id, requested_url, final_url, fetched_at, status_code,
                error, article_id, version_id, raw_sha256, raw_path, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, url, details.pop("final_url", None), details.pop("fetched_at", None) or _now(),
             status_code, error, article_id, version_id, details.pop("raw_sha256", None),
             details.pop("raw_path", None), _json(details)),
        )
        return cursor.lastrowid

    def record_fetch(self, run_id: int | None, url: str, status_code: int | None = None,
                     error: str | None = None, article_id: int | None = None,
                     version_id: int | None = None, **details: Any) -> int:
        with self.connection:
            return self._insert_fetch(run_id, url, status_code, error, article_id, version_id, **details)

    def has_article(self, url: str) -> bool:
        row = self.connection.execute(
            """SELECT 1 FROM source_articles a WHERE a.current_version_id IS NOT NULL
                AND (a.canonical_url=? OR a.source_url=? OR EXISTS (
                    SELECT 1 FROM source_fetches f WHERE f.article_id=a.id
                    AND (f.requested_url=? OR f.final_url=?) AND f.error IS NULL)) LIMIT 1""",
            (url, url, url, url),
        ).fetchone()
        return row is not None

    def _snapshot(self, raw_html: str | bytes) -> tuple[str, str]:
        raw = raw_html.encode("utf-8") if isinstance(raw_html, str) else raw_html
        if not isinstance(raw, bytes):
            raise TypeError("raw_html must be a string or bytes")
        digest = hashlib.sha256(raw).hexdigest()
        target = self.raw_dir / digest[:2] / f"{digest}.html.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(gzip.compress(raw, mtime=0))
                os.replace(temporary, target)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        # Relative paths keep the database + raw folder portable together.
        base = self.path.parent if self.path else self.raw_dir.parent
        return digest, os.path.relpath(target, base).replace(os.sep, "/")

    def store_article(self, result: ExtractionResult, *, raw_html: str | bytes,
                      requested_url: str | None = None, status_code: int | None = 200,
                      run_id: int | None = None, fetched_at: str | None = None) -> dict[str, Any]:
        parsed = urlsplit(result.canonical_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Extraction must have an absolute HTTP(S) canonical URL")
        if not result.title.strip() or not result.blocks or not result.clean_text.strip():
            raise ValueError("Refusing to store an extraction with no title or clean content")
        captured = fetched_at or _now()
        content_hash = _fingerprint(result)
        raw_hash, raw_path = self._snapshot(raw_html)
        review = bool(result.quality_flags) or any(event.get("requires_review") for event in result.events)
        # Snapshot writes are atomic and content-addressed. If SQL fails the snapshot
        # may remain unreferenced, but no partial article/version can be committed.
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO source_sites(hostname, base_url, created_at) VALUES (?, ?, ?)",
                (parsed.hostname.lower(), f"{parsed.scheme}://{parsed.netloc}", captured),
            )
            site_id = self.connection.execute("SELECT id FROM source_sites WHERE hostname=?", (parsed.hostname.lower(),)).fetchone()[0]
            self.connection.execute(
                """INSERT OR IGNORE INTO source_articles(site_id, canonical_url, source_url, title,
                    language, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (site_id, result.canonical_url, result.url, result.title, result.language, captured, captured),
            )
            article_id = self.connection.execute("SELECT id FROM source_articles WHERE canonical_url=?", (result.canonical_url,)).fetchone()[0]
            previous = self.connection.execute(
                "SELECT id FROM article_versions WHERE article_id=? AND content_hash=?", (article_id, content_hash),
            ).fetchone()
            created = previous is None
            if previous:
                version_id = previous[0]
            else:
                cursor = self.connection.execute(
                    """INSERT INTO article_versions(article_id, content_hash, cleaner_version, title,
                        language, published_at, modified_at, captured_at, metadata_json, quality_flags_json,
                        review_required, original_text, clean_text, raw_sha256, raw_path, word_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (article_id, content_hash, result.cleaner_version, result.title, result.language,
                     result.published_at, result.modified_at, captured, _json(result.metadata),
                     _json(result.quality_flags), int(review), result.original_text, result.clean_text,
                     raw_hash, raw_path, len(result.clean_text.split())),
                )
                version_id = cursor.lastrowid
                self._insert_content(version_id, result)
            self.connection.execute(
                "UPDATE source_articles SET title=?, language=?, current_version_id=?, last_seen_at=MAX(last_seen_at, ?) WHERE id=?",
                (result.title, result.language, version_id, captured, article_id),
            )
            fetch_id = self._insert_fetch(
                run_id, requested_url or result.url, status_code, article_id=article_id,
                version_id=version_id, final_url=result.url, fetched_at=captured,
                raw_sha256=raw_hash, raw_path=raw_path,
            )
        return {"article_id": article_id, "version_id": version_id, "created_version": created, "fetch_id": fetch_id}

    def _insert_content(self, version_id: int, result: ExtractionResult) -> None:
        for index, block in enumerate(result.blocks):
            self.connection.execute(
                """INSERT INTO article_blocks(version_id, block_key, position, source_order, kind, text,
                    heading_level, section_path_json, data_json, flags_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, block["key"], block.get("position", index), block.get("source_order", index),
                 block["kind"], block["text"], block.get("heading_level"), _json(block.get("section_path", [])),
                 _json(block.get("data", {})), _json(block.get("flags", []))),
            )
        for link in result.links:
            self.connection.execute(
                """INSERT INTO article_links(version_id, block_key, source_order, url, anchor, kind,
                    disposition) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (version_id, link.get("block_key"), link.get("source_order"), link["url"],
                 link.get("anchor", ""), link.get("kind", "unknown"), link.get("disposition", "removed")),
            )
        for event in result.events:
            self.connection.execute(
                """INSERT INTO extraction_events(version_id, source_order, reason, original_text,
                    selector, requires_review, prev_block_key, next_block_key, details_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, event.get("source_order"), event["reason"], event.get("text", ""),
                 event.get("selector"), int(bool(event.get("requires_review"))), event.get("prev_block_key"),
                 event.get("next_block_key"), _json(event.get("details", {}))),
            )

    def _article(self, url: str | None, article_id: int | None) -> sqlite3.Row:
        if (url is None) == (article_id is None):
            raise ValueError("Provide exactly one of url or article_id")
        if article_id is not None:
            article = self.connection.execute("SELECT * FROM source_articles WHERE id=?", (article_id,)).fetchone()
        else:
            article = self.connection.execute(
                """SELECT a.* FROM source_articles a WHERE a.canonical_url=? OR a.source_url=?
                    OR EXISTS (SELECT 1 FROM source_fetches f WHERE f.article_id=a.id
                        AND (f.requested_url=? OR f.final_url=?) AND f.error IS NULL)
                    ORDER BY a.canonical_url=? DESC LIMIT 1""",
                (url, url, url, url, url),
            ).fetchone()
        if article is None or article["current_version_id"] is None:
            raise KeyError(f"No stored article: {article_id if article_id is not None else url}")
        return article

    def get_snapshot(self, article_id: int) -> dict[str, str]:
        """Read the current version's local source for offline re-cleaning.

        No writes or network requests occur here. Missing files, damaged gzip
        streams, unexpected paths, invalid UTF-8, and hash mismatches fail loudly;
        callers must never replace a good version with partial recovered content.
        Snapshots created by the HTTP collector are UTF-8 encoded HTML strings.
        """
        article = self._article(None, article_id)
        version = self.connection.execute(
            "SELECT id, raw_path, raw_sha256 FROM article_versions WHERE id=?",
            (article["current_version_id"],),
        ).fetchone()
        base = self.path.parent if self.path else self.raw_dir.parent
        path = (base / version["raw_path"]).resolve()
        if not path.is_relative_to(self.raw_dir.resolve()):
            raise ValueError("Snapshot path is outside the configured raw snapshot directory")
        raw = gzip.decompress(path.read_bytes())
        if hashlib.sha256(raw).hexdigest() != version["raw_sha256"]:
            raise ValueError(f"Snapshot integrity check failed for article {article_id}")
        html = raw.decode("utf-8")
        fetch = self.connection.execute(
            """SELECT final_url FROM source_fetches WHERE version_id=? AND raw_sha256=?
                AND final_url IS NOT NULL ORDER BY id DESC LIMIT 1""",
            (version["id"], version["raw_sha256"]),
        ).fetchone()
        return {"url": fetch["final_url"] if fetch else article["source_url"], "html": html}

    def get_export(self, url: str | None = None, article_id: int | None = None) -> dict[str, Any]:
        article = self._article(url, article_id)
        version = self.connection.execute("SELECT * FROM article_versions WHERE id=?", (article["current_version_id"],)).fetchone()
        blocks = []
        for row in self.connection.execute("SELECT * FROM article_blocks WHERE version_id=? ORDER BY position", (version["id"],)):
            blocks.append({
                "id": row["block_key"], "position": row["position"], "source_order": row["source_order"],
                "kind": row["kind"], "text": row["text"], "heading_level": row["heading_level"],
                "section_path": json.loads(row["section_path_json"]), "data": json.loads(row["data_json"]),
                "flags": json.loads(row["flags_json"]),
            })
        references = []
        for row in self.connection.execute(
            "SELECT * FROM article_links WHERE version_id=? AND kind='external_reference' AND disposition='retained' ORDER BY id",
            (version["id"],),
        ):
            references.append({"block_id": row["block_key"], "url": row["url"], "anchor": row["anchor"]})
        # Never export original_text, selector, event.details, authors, reviewers,
        # or raw snapshots. Gaps identify context without leaking quarantined prose.
        gaps = [{
            "id": row["id"], "reason": row["reason"], "source_order": row["source_order"],
            "previous_block_id": row["prev_block_key"], "next_block_id": row["next_block_key"],
            "requires_review": bool(row["requires_review"]),
        } for row in self.connection.execute("SELECT * FROM extraction_events WHERE version_id=? ORDER BY source_order, id", (version["id"],))]
        metadata = json.loads(version["metadata_json"])
        return {
            "schema_version": SCHEMA_VERSION,
            "article": {
                "id": article["id"], "version_id": version["id"], "source_url": article["canonical_url"],
                "title": version["title"], "language": version["language"],
                "published_at": version["published_at"], "modified_at": version["modified_at"],
                "captured_at": version["captured_at"], "cleaner_version": version["cleaner_version"],
                "content_hash": version["content_hash"],
                "categories": metadata.get("categories", []), "tags": metadata.get("tags", []),
            },
            "content": {"clean_text": version["clean_text"], "blocks": blocks},
            "references": references,
            "gaps": gaps,
            "quality": {"review_required": bool(version["review_required"]), "flags": json.loads(version["quality_flags_json"])},
            "adaptation_instructions": [
                "Use only content blocks as source prose; metadata and gaps are context, not article text.",
                "Do not invent text, facts, quotes, personal experience, credentials, or data to fill omissions.",
                "Omitted promotional or navigation blocks require no replacement; join the remaining blocks in order.",
                "Review flagged gaps and dangling transitions before adaptation; omit unsupported sections or obtain another source.",
                "Preserve the hierarchy, lists, table rows and cells, and question-answer relationships.",
                "External references retain provenance; source-site URLs are not destination internal links.",
                "Extraction does not verify the accuracy or currency of legal, tax, health, or other claims.",
            ],
        }

    def list_articles(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT a.id, a.canonical_url, a.source_url, a.title, a.language, a.current_version_id,
                a.first_seen_at, a.last_seen_at, v.word_count, v.review_required,
                v.published_at, v.modified_at, v.cleaner_version,
                (SELECT COUNT(*) FROM article_versions av WHERE av.article_id=a.id) AS version_count
                FROM source_articles a JOIN article_versions v ON v.id=a.current_version_id ORDER BY a.id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        tables = {
            "sites": "source_sites", "articles": "source_articles", "versions": "article_versions",
            "blocks": "article_blocks", "links": "article_links", "events": "extraction_events",
            "runs": "crawl_runs", "fetches": "source_fetches",
        }
        counts = {key: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for key, table in tables.items()}
        counts["review_required"] = self.connection.execute(
            "SELECT COUNT(*) FROM source_articles a JOIN article_versions v ON v.id=a.current_version_id WHERE v.review_required=1"
        ).fetchone()[0]
        counts["fetch_errors"] = self.connection.execute("SELECT COUNT(*) FROM source_fetches WHERE error IS NOT NULL").fetchone()[0]
        counts["schema_version"] = self.connection.execute("PRAGMA user_version").fetchone()[0]
        return counts
