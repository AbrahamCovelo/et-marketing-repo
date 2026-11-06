"""Persist deterministic formatting separately from the original model response.

No provider is called here. A manual editorial revision always takes precedence;
successful repairs are anchored to the exact original output and are idempotent.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from .html_normalization import NORMALIZATION_POLICY, normalize_generated_html
from .trial import get_trial


_SCHEMA = """CREATE TABLE IF NOT EXISTS generation_html_formats (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES generation_trials(id),
    policy TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    html TEXT NOT NULL,
    report_json TEXT NOT NULL CHECK(json_valid(report_json)),
    created_at TEXT NOT NULL,
    UNIQUE(trial_id,source_sha256,policy)
)"""


def _sha(html):
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _report(result, status):
    # Do not duplicate the article in report_json or pass raw model responses.
    return {"status": status, **{key: result[key] for key in (
        "policy", "changed", "valid", "validation", "original_validation", "operations", "reason"
    ) if key in result}}


def ensure_trial_format(db_path, trial_id):
    """Return current content, repairing only unedited completed model output.

The pure normalizer must prove preservation and pass the existing semantic
validator. Failed/ambiguous repairs never become effective content. Nothing
changes in generation_trials, its request, response, usage or original HTML.
"""
    trial = get_trial(db_path, trial_id)
    if trial["status"] != "completed":
        raise ValueError("Solo se puede normalizar una adaptación completa.")
    if trial.get("edit_version"):
        trial["normalization"] = {"status": "editor", "changed": False,
                                  "reason": "Se conserva la edición manual vigente."}
        return trial
    if trial.get("html_source") == "normalized" and trial.get("normalization", {}).get("policy") == NORMALIZATION_POLICY:
        return trial
    original = trial["original_html"]
    result = normalize_generated_html(original)
    if not result["valid"] or not result["changed"]:
        trial["normalization"] = _report(result, "unchanged" if result["valid"] else "unresolved")
        return trial
    source_hash = _sha(original)
    report = _report(result, "normalized")
    with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = db.execute("SELECT html,status FROM generation_trials WHERE id=?", (trial_id,)).fetchone()
        if not row or ("adaptation_deletions" in tables and db.execute(
                "SELECT 1 FROM adaptation_deletions WHERE trial_id=?", (trial_id,)).fetchone()):
            raise KeyError("La adaptación fue eliminada antes de normalizar su formato.")
        if row["status"] != "completed" or _sha(row["html"]) != source_hash:
            raise ValueError("La salida original cambió durante la normalización; no se guardó el resultado.")
        has_manual_edit = "adaptation_edits" in tables and db.execute(
            "SELECT 1 FROM adaptation_edits WHERE trial_id=?", (trial_id,)).fetchone()
        if not has_manual_edit:
            for table, key, statuses in (("adaptation_actions", "trial_id", "'running','unknown'"),
                                         ("deployment_trials", "generation_trial_id", "'sending','unknown'")):
                if table in tables and db.execute(
                        f"SELECT 1 FROM {table} WHERE {key}=? AND status IN ({statuses})", (trial_id,)).fetchone():
                    raise ValueError("Hay un envío en curso o incierto; no se cambió el formato local.")
            db.execute(_SCHEMA)
            db.execute("""INSERT OR IGNORE INTO generation_html_formats
                (trial_id,policy,source_sha256,normalized_sha256,html,report_json,created_at)
                VALUES (?,?,?,?,?,?,?)""", (trial_id, NORMALIZATION_POLICY, source_hash, _sha(result["html"]),
                result["html"], json.dumps(report, ensure_ascii=False, allow_nan=False),
                datetime.now(timezone.utc).isoformat(timespec="microseconds")))
    # A concurrent manual save is respected instead of being overwritten by an
    # earlier normalizer's snapshot. Callers always receive the fresh revision.
    return get_trial(db_path, trial_id)
