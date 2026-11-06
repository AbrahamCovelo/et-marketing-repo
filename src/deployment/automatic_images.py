"""Deterministic cover selection for explicitly requested automatic trial runs.

The selector chooses from validated CC0 catalogues without a human image picker.
It is a keyword/format policy, not a visual model or an assurance of relevance.
The chosen candidate is persisted before import; imports retain the existing
once-only media journal. This module never creates or updates WordPress posts.
"""
from contextlib import closing
import json
import sqlite3

from . import images
from .repository import _id


POLICY = "cc0-topic-first-v1"
_SCHEMA = """CREATE TABLE IF NOT EXISTS cover_auto_selections (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES source_articles(id),
    source_version_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL REFERENCES deployment_targets(id),
    policy TEXT NOT NULL,
    source_title TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('searching','selected','importing','ready','needs_image','failed','unknown')),
    provider TEXT NOT NULL DEFAULT '', query TEXT NOT NULL DEFAULT '',
    candidate_id INTEGER REFERENCES cover_candidates(id),
    import_id INTEGER REFERENCES cover_imports(id),
    wp_media_id INTEGER, wp_media_url TEXT NOT NULL DEFAULT '',
    decision_json TEXT NOT NULL CHECK(json_valid(decision_json)),
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(article_id,source_version_id,target_id,policy),
    FOREIGN KEY(source_version_id,article_id) REFERENCES article_versions(id,article_id)
)"""


def automatic_queries(article_title):
    """Prefer the actual topic to a generic visa/document illustration."""
    tokens = images._tokens(images._plain(article_title, 1000))
    base = images.suggest_queries(article_title)
    if (tokens & {"nomad", "nomads", "nomada", "nomadas", "teletrabajo"}
            or tokens & {"remote", "remotely"} and tokens & {"work", "working"}):
        return ["remote work", "laptop", "workspace", "paperwork"]
    return base or (["Spain"] if tokens & {"spain", "espana"} else [])


def _record(row):
    result = dict(row)
    result["selection_id"] = result["id"]
    result["decision"] = json.loads(result.pop("decision_json"))
    result["selection_mode"] = "automatic"
    return result


def get_auto_selection(db_path, selection_id):
    """Read the durable decision without any catalogue or WordPress request."""
    _id(selection_id)
    row = images._read_one(db_path, "cover_auto_selections",
                          "SELECT * FROM cover_auto_selections WHERE id=?", (selection_id,))
    return _record(row) if row else None


def _set(db_path, selection_id, status, **fields):
    allowed = {"provider", "query", "candidate_id", "import_id", "wp_media_id",
               "wp_media_url", "decision_json", "error"}
    if set(fields) - allowed:
        raise ValueError("Campos de selección no admitidos.")
    fields.update(status=status, updated_at=images._now())
    with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("UPDATE cover_auto_selections SET " + ",".join(k + "=?" for k in fields)
                   + " WHERE id=?", (*fields.values(), selection_id))
    return get_auto_selection(db_path, selection_id)


def _eligible(candidate, query):
    if not images._candidate_valid(candidate) or type(candidate.get("id")) is not int:
        return False
    words = images._tokens(candidate["title"])
    if query == "remote work":
        return bool(words & {"laptop", "computer", "desk", "workspace", "office", "workstation"}
                    or {"remote", "work"} <= words)
    if query == "laptop":
        return bool(words & {"laptop", "laptops", "macbook", "notebook"})
    if query == "workspace":
        return bool(words & {"workspace", "workstation", "desk", "office"})
    return bool(words & images._tokens(query))


def _apply_import(db_path, selection_id, result):
    status = result.get("status")
    if status not in {"ready", "failed", "unknown", "importing"}:
        return _set(db_path, selection_id, "unknown",
                    error="La importación no confirmó un estado válido; no se repetirá automáticamente.")
    return _set(db_path, selection_id, status, import_id=result["id"],
                wp_media_id=result.get("wp_media_id"), wp_media_url=result.get("wp_media_url", ""),
                error=result.get("error", ""))


def _resume(db_path, selection, env_path, session):
    if selection["status"] in {"ready", "needs_image", "failed", "searching"}:
        return selection
    if selection["status"] in {"importing", "unknown"}:
        # Recover only by reading the importer journal. Never repeat its POST.
        existing = images.get_import_record(db_path, selection["target_id"], selection["candidate_id"])
        return _apply_import(db_path, selection["id"], existing) if existing else selection
    with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
        claimed = db.execute("""UPDATE cover_auto_selections SET status='importing',updated_at=?
            WHERE id=? AND status='selected'""", (images._now(), selection["id"])).rowcount
    if not claimed:
        return get_auto_selection(db_path, selection["id"])
    try:
        result = images.import_candidate(db_path, selection["target_id"], selection["candidate_id"],
                                         env_path=env_path, session=session)
    except (ValueError, KeyError):
        existing = images.get_import_record(db_path, selection["target_id"], selection["candidate_id"])
        if existing:
            return _apply_import(db_path, selection["id"], existing)
        return _set(db_path, selection["id"], "failed",
                    error="La imagen no superó la validación o faltan ajustes del destino. No se importó.")
    return _apply_import(db_path, selection["id"], result)


def auto_import_cover(db_path, target_id, article_id, env_path=None, session=None):
    """Choose and import one automatic cover, returning its durable selection.

    Selection order: WordPress Photos before Openverse, topic query priority,
    then caption/query score and external ID. No visual or manual choice occurs.
    An interrupted/uncertain import is never retried by calling this again.
    """
    _id(target_id)
    _id(article_id)
    images._initialize(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute(_SCHEMA)
        source = db.execute("""SELECT a.current_version_id,v.title FROM source_articles a
            JOIN article_versions v ON v.id=a.current_version_id AND v.article_id=a.id WHERE a.id=?""",
                            (article_id,)).fetchone()
        if source is None:
            raise KeyError("No existe el artículo fuente para seleccionar la portada.")
        if not db.execute("SELECT 1 FROM deployment_targets WHERE id=?", (target_id,)).fetchone():
            raise KeyError("No existe el destino de WordPress.")
        previous = db.execute("""SELECT * FROM cover_auto_selections
            WHERE article_id=? AND source_version_id=? AND target_id=? AND policy=?""",
                              (article_id, source["current_version_id"], target_id, POLICY)).fetchone()
        if previous:
            selection = _record(previous)
        else:
            now = images._now()
            queries = automatic_queries(source["title"])
            decision = {"mode": "automatic", "policy": POLICY, "queries": queries, "searches": [],
                        "rule": "provider priority, topic query priority, caption/format score, external ID",
                        "visual_model_used": False, "human_image_selection": False}
            cursor = db.execute("""INSERT INTO cover_auto_selections
                (article_id,source_version_id,target_id,policy,source_title,status,decision_json,created_at,updated_at)
                VALUES (?,?,?,?,?,'searching',?,?,?)""",
                (article_id, source["current_version_id"], target_id, POLICY, source["title"],
                 images._json(decision), now, now))
            selection = _record(db.execute("SELECT * FROM cover_auto_selections WHERE id=?",
                                           (cursor.lastrowid,)).fetchone())
    if previous:
        return _resume(db_path, selection, env_path, session)
    decision = selection["decision"]
    for provider in ("wordpress_photos", "openverse"):
        for query in decision["queries"]:
            try:
                result = images.search_candidates(db_path, query, provider, session=session)
            except ValueError:
                decision["searches"].append({"provider": provider, "query": query, "status": "catalogue_error"})
                continue
            candidates = [item for item in result["candidates"] if _eligible(item, query)]
            decision["searches"].append({"provider": provider, "query": query, "status": result["status"],
                                        "validated_candidates": len(result["candidates"]),
                                        "topic_candidates": len(candidates)})
            if not candidates:
                continue
            chosen = sorted(candidates, key=lambda item: (-images._score(item, query), item["external_id"]))[0]
            decision["candidate"] = {key: chosen[key] for key in (
                "id", "external_id", "title", "source_url", "image_url", "creator", "license", "license_url",
                "width", "height")}
            decision["candidate"]["score"] = images._score(chosen, query)
            selection = _set(db_path, selection["id"], "selected", provider=provider, query=query,
                             candidate_id=chosen["id"], decision_json=images._json(decision))
            return _resume(db_path, selection, env_path, session)
    return _set(db_path, selection["id"], "needs_image", decision_json=images._json(decision),
                error="No se encontró una fotografía CC0 apta y relacionada mediante las búsquedas automáticas.")
