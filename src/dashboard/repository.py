"""Queries for the local dashboard, separate from immutable source versions.

Only ``dashboard_selection`` is owned by this module. Source rows are never
edited here; every request opens its own SQLite connection so the UI and the
background collector can work at the same time.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from typing import Any
import unicodedata

from src.extraction.db_manager import Database


DATE_FIELDS = frozenset({"published_at", "modified_at", "captured_at", "last_adapted_at"})
_MAX_SQLITE_ID = 2**63 - 1
# Bogotá has no daylight-saving change in the period covered by this corpus.
BOGOTA = timezone(timedelta(hours=-5), "America/Bogota")
_SELECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_selection (
    article_id INTEGER PRIMARY KEY REFERENCES source_articles(id) ON DELETE CASCADE,
    selected_at TEXT NOT NULL
)
"""
_SORTS = {
    "published_desc": "v.published_at IS NULL, v.published_at DESC, a.id DESC",
    "modified_desc": "v.modified_at IS NULL, v.modified_at DESC, a.id DESC",
    "captured_desc": "v.captured_at DESC, a.id DESC",
    "title_asc": "fold_text(v.title) ASC, a.id ASC",
    "adapted_desc": "adapted.last_adapted_jd IS NULL, adapted.last_adapted_jd DESC, a.id DESC",
}


def _fold_text(value: str | None) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    ).casefold()


def _date_input(value: str | None, label: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} debe tener formato AAAA-MM-DD.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} no es una fecha válida.") from exc
    return value


def validate_date_range(date_from: str | None = None, date_to: str | None = None) -> tuple[str | None, str | None]:
    """Validate inclusive calendar-day bounds; blank inputs mean unbounded."""
    start = _date_input(date_from, "La fecha inicial")
    end = _date_input(date_to, "La fecha final")
    if start and end and start > end:
        raise ValueError("La fecha inicial no puede ser posterior a la fecha final.")
    return start, end


def source_calendar_date(value: str | None, date_field: str = "published_at") -> str | None:
    """Use the publisher's calendar date, or Bogotá's day for capture times.

    Missing and malformed source dates remain unknown, never substituted with
    the capture date. Naive capture timestamps are interpreted as UTC.
    """
    if date_field not in DATE_FIELDS:
        raise ValueError("Campo de fecha no válido.")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date_field in {"captured_at", "last_adapted_at"}:
            parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            return parsed.astimezone(BOGOTA).date().isoformat()
        return parsed.date().isoformat()
    except ValueError:
        return None


def date_in_range(value: str | None, date_from: str | None = None,
                  date_to: str | None = None, *, date_field: str = "published_at") -> bool:
    """Compare one source timestamp against inclusive date bounds."""
    if date_field not in DATE_FIELDS:
        raise ValueError("Campo de fecha no válido.")
    start, end = validate_date_range(date_from, date_to)
    if not start and not end:
        return True
    day = source_calendar_date(value, date_field)
    return day is not None and (not start or day >= start) and (not end or day <= end)


def _positive_int(value: Any, name: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or (maximum is not None and value > maximum):
        suffix = f" entre 1 y {maximum}" if maximum is not None else " mayor que cero"
        raise ValueError(f"{name} debe ser un entero{suffix}.")
    return value


def _decode(row, json_fields: dict[str, str] | None = None, bool_fields=()) -> dict:
    item = dict(row)
    for source, destination in (json_fields or {}).items():
        item[destination] = json.loads(item.pop(source))
    for field in bool_fields:
        if field in item:
            item[field] = bool(item[field])
    return item


class Repository:
    def __init__(self, db_path: str | Path = "data/source_articles.db"):
        if str(db_path) == ":memory:":
            raise ValueError("El panel requiere una base SQLite en archivo.")
        self.db_path = Path(db_path).resolve()
        with Database(self.db_path) as db:
            with db.connection:
                db.connection.execute(_SELECTION_SCHEMA)

    @contextmanager
    def _db(self):
        with Database(self.db_path) as db:
            db.connection.create_function("fold_text", 1, _fold_text, deterministic=True)
            db.connection.create_function("article_date", 2, source_calendar_date, deterministic=True)
            yield db

    def summary(self) -> dict[str, Any]:
        with self._db() as db:
            row = db.connection.execute("""
                SELECT COUNT(*) AS articles,
                       COALESCE(SUM(v.review_required), 0) AS review_required,
                       COALESCE(SUM(v.word_count), 0) AS words,
                       COALESCE(SUM(article_date(v.published_at, 'published_at') IS NULL), 0) AS missing_publication_dates,
                       COALESCE(SUM(article_date(v.modified_at, 'modified_at') IS NULL), 0) AS missing_modification_dates
                FROM source_articles a JOIN article_versions v ON a.current_version_id=v.id
            """).fetchone()
            result = dict(row)
            result["versions"] = db.connection.execute("SELECT COUNT(*) FROM article_versions").fetchone()[0]
            result["selected_count"] = db.connection.execute("SELECT COUNT(*) FROM dashboard_selection").fetchone()[0]
            latest = db.connection.execute("SELECT started_at FROM crawl_runs ORDER BY id DESC LIMIT 1").fetchone()
            result["last_run_at"] = latest[0] if latest else None
            return result

    def _article_query(self, q="", date_field="published_at", date_from=None,
                       date_to=None, review="all", selected_only=False, sort="published_desc",
                       adaptation="all", *, connection):
        if not isinstance(q, str) or len(q) > 500:
            raise ValueError("El texto de búsqueda debe tener hasta 500 caracteres.")
        if date_field not in DATE_FIELDS:
            raise ValueError("Campo de fecha no válido.")
        review = {"needs_review": "required", "clean": "clear"}.get(review, review)
        if review not in {"all", "required", "clear"}:
            raise ValueError("Filtro de revisión no válido.")
        if not isinstance(selected_only, bool):
            raise ValueError("El filtro de selección debe ser booleano.")
        if sort not in _SORTS:
            raise ValueError("Orden no válido.")
        if adaptation not in {'all', 'adapted', 'not_adapted'}:
            raise ValueError('Filtro de adaptaciones no válido.')
        start, end = validate_date_range(date_from, date_to)
        q = q.strip()
        clauses: list[str] = []
        parameters: list[Any] = []
        if q:
            clauses.append("instr(fold_text(v.title || char(10) || v.clean_text || char(10) || a.canonical_url), ?) > 0")
            parameters.append(_fold_text(q))
        date_expression = 'adapted.last_adapted_at' if date_field == 'last_adapted_at' else f'v.{date_field}'
        if start:
            clauses.append(f"article_date({date_expression}, ?) >= ?")
            parameters.extend([date_field, start])
        if end:
            clauses.append(f"article_date({date_expression}, ?) <= ?")
            parameters.extend([date_field, end])
        if review != "all":
            clauses.append("v.review_required=?")
            parameters.append(int(review == "required"))
        if selected_only:
            clauses.append("s.article_id IS NOT NULL")
        if adaptation != 'all':
            clauses.append('adapted.article_id IS ' + ('NOT NULL' if adaptation == 'adapted' else 'NULL'))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        joins = """FROM source_articles a
            JOIN article_versions v ON a.current_version_id=v.id
            LEFT JOIN dashboard_selection s ON s.article_id=a.id"""
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'generation_trials' in tables:
            excluded = (' AND NOT EXISTS (SELECT 1 FROM adaptation_deletions d WHERE d.trial_id=t.id)'
                        if 'adaptation_deletions' in tables else '')
            aggregate = f"""SELECT t.article_id,
                MAX(julianday(COALESCE(t.completed_at,t.created_at))) AS last_adapted_jd,
                strftime('%Y-%m-%dT%H:%M:%fZ',MAX(julianday(COALESCE(t.completed_at,t.created_at)))) AS last_adapted_at
                FROM generation_trials t WHERE t.status='completed'{excluded} GROUP BY t.article_id"""
        else:
            aggregate = 'SELECT NULL AS article_id,NULL AS last_adapted_jd,NULL AS last_adapted_at WHERE 0'
        joins += f' LEFT JOIN ({aggregate}) adapted ON adapted.article_id=a.id'
        return joins, where, parameters

    def list_articles(self, q: str = "", date_field: str = "published_at", date_from: str | None = None,
                      date_to: str | None = None, review: str = "all", selected_only: bool = False,
                      page: int = 1, page_size: int = 25, sort: str = "published_desc", adaptation: str = 'all') -> dict[str, Any]:
        page = _positive_int(page, "La página", 1_000_000)
        page_size = _positive_int(page_size, "El tamaño de página", 100)
        with self._db() as db:
            joins, where, parameters = self._article_query(q, date_field, date_from, date_to, review, selected_only, sort, adaptation, connection=db.connection)
            total = db.connection.execute(f"SELECT COUNT(*) {joins}{where}", parameters).fetchone()[0]
            pages = max(1, math.ceil(total / page_size))
            rows = db.connection.execute(f"""
                SELECT a.id, v.title, a.canonical_url, v.published_at, v.modified_at,
                       v.captured_at, v.word_count, v.review_required, a.current_version_id, adapted.last_adapted_at,
                       s.article_id IS NOT NULL AS selected,
                       (SELECT COUNT(*) FROM article_versions av WHERE av.article_id=a.id) AS version_count,
                       substr(v.clean_text, 1, 220) AS snippet
                {joins}{where} ORDER BY {_SORTS[sort]} LIMIT ? OFFSET ?
            """, [*parameters, page_size, (page - 1) * page_size]).fetchall()
        return {
            "items": [_decode(row, bool_fields=("selected", "review_required")) for row in rows],
            "total": total, "page": page, "page_size": page_size, "pages": pages,
        }

    def select_matching(self, **filters) -> dict[str, int]:
        """Select all matching pages atomically using the same predicates as the list."""
        if set(filters) - {"q", "date_field", "date_from", "date_to", "review", "selected_only", "sort", 'adaptation'}:
            raise ValueError("La selección por filtros contiene campos no admitidos.")
        with self._db() as db, db.connection:
            db.connection.execute("BEGIN IMMEDIATE")
            joins, where, parameters = self._article_query(**filters, connection=db.connection)
            total = db.connection.execute(f"SELECT COUNT(*) {joins}{where}", parameters).fetchone()[0]
            if total > 5000:
                raise ValueError("La selección admite hasta 5000 resultados. Reduzca el rango o aplique más filtros.")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            result = db.connection.execute(f"""INSERT OR IGNORE INTO dashboard_selection(article_id,selected_at)
                SELECT a.id, ? {joins}{where}""", [now, *parameters])
            count = db.connection.execute("SELECT COUNT(*) FROM dashboard_selection").fetchone()[0]
            return {"selected_count": count, "matched_count": total, "added_count": result.rowcount}

    def detail(self, article_id: int) -> dict[str, Any]:
        article_id = _positive_int(article_id, "El identificador del artículo", _MAX_SQLITE_ID)
        with self._db() as db:
            row = db.connection.execute("""
                SELECT a.id, a.canonical_url, a.source_url, a.current_version_id,
                       a.first_seen_at, a.last_seen_at, v.title, v.language,
                       v.published_at, v.modified_at, v.captured_at, v.word_count,
                       v.cleaner_version, v.content_hash, v.review_required, v.metadata_json,
                       s.article_id IS NOT NULL AS selected,
                       (SELECT COUNT(*) FROM article_versions av WHERE av.article_id=a.id) AS version_count
                FROM source_articles a JOIN article_versions v ON a.current_version_id=v.id
                LEFT JOIN dashboard_selection s ON s.article_id=a.id WHERE a.id=?
            """, (article_id,)).fetchone()
            if row is None:
                raise KeyError(f"No existe el artículo {article_id}.")
            article = _decode(row, {"metadata_json": "metadata"}, ("review_required", "selected"))
            version_id = article["current_version_id"]
            version = db.connection.execute(
                "SELECT clean_text, original_text, quality_flags_json FROM article_versions WHERE id=?", (version_id,),
            ).fetchone()
            blocks = []
            for block in db.connection.execute("SELECT * FROM article_blocks WHERE version_id=? ORDER BY position", (version_id,)):
                item = _decode(block, {"section_path_json": "section_path", "data_json": "data", "flags_json": "flags"})
                item["id"] = item["block_key"]
                item["key"] = item["block_key"]
                blocks.append(item)
            events = [
                _decode(event, {"details_json": "details"}, ("requires_review",))
                for event in db.connection.execute("SELECT * FROM extraction_events WHERE version_id=? ORDER BY source_order,id", (version_id,))
            ]
            links = [dict(link) for link in db.connection.execute("SELECT * FROM article_links WHERE version_id=? ORDER BY source_order,id", (version_id,))]
            versions = [
                _decode(item, {"quality_flags_json": "quality_flags"}, ("review_required", "current"))
                for item in db.connection.execute("""
                    SELECT id, captured_at, cleaner_version, title, published_at, modified_at,
                           word_count, review_required, quality_flags_json, id=? AS current
                    FROM article_versions WHERE article_id=? ORDER BY id DESC
                """, (version_id, article_id))
            ]
            return {
                "article": article, "clean_text": version["clean_text"],
                "original_text": version["original_text"], "blocks": blocks,
                "events": events, "links": links, "versions": versions,
                "quality_flags": json.loads(version["quality_flags_json"]),
            }

    def set_selection(self, article_ids: list[int], selected: bool) -> dict[str, int]:
        if not isinstance(article_ids, list) or len(article_ids) > 5000:
            raise ValueError("La selección debe ser una lista de hasta 5000 identificadores.")
        if not isinstance(selected, bool):
            raise ValueError("El estado de selección debe ser booleano.")
        article_ids = sorted({_positive_int(value, "El identificador del artículo", _MAX_SQLITE_ID) for value in article_ids})
        with self._db() as db:
            with db.connection:
                # Check every ID before making any write. Chunks also support SQLite
                # builds with conservative parameter limits.
                existing: set[int] = set()
                for index in range(0, len(article_ids), 500):
                    chunk = article_ids[index:index + 500]
                    marks = ",".join("?" for _ in chunk)
                    existing.update(row[0] for row in db.connection.execute(
                        f"SELECT id FROM source_articles WHERE current_version_id IS NOT NULL AND id IN ({marks})", chunk,
                    ))
                missing = set(article_ids) - existing
                if missing:
                    raise ValueError("La selección contiene artículos que no existen: " + ", ".join(map(str, sorted(missing)[:10])))
                if selected:
                    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    db.connection.executemany(
                        "INSERT OR IGNORE INTO dashboard_selection(article_id,selected_at) VALUES (?,?)",
                        [(article_id, now) for article_id in article_ids],
                    )
                else:
                    db.connection.executemany("DELETE FROM dashboard_selection WHERE article_id=?", [(article_id,) for article_id in article_ids])
            count = db.connection.execute("SELECT COUNT(*) FROM dashboard_selection").fetchone()[0]
            return {"selected_count": count}

    def selected_ids(self) -> list[int]:
        with self._db() as db:
            return [row[0] for row in db.connection.execute("SELECT article_id FROM dashboard_selection ORDER BY article_id")]

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = _positive_int(limit, "El límite", 100)
        with self._db() as db:
            return [
                _decode(row, {"config_json": "config", "stats_json": "stats"})
                for row in db.connection.execute("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT ?", (limit,))
            ]
