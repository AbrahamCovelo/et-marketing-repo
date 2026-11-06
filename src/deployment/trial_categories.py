"""Local category corrections for saved adaptations; no model or HTTP calls."""
import json

from src.generation.trial import get_trial
from .categories import _connect, _digest, _exists, _id, _now, _read_catalog, _target


SCHEMA = """CREATE TABLE IF NOT EXISTS adaptation_category_overrides (
    trial_id INTEGER PRIMARY KEY REFERENCES generation_trials(id),
    target_id INTEGER NOT NULL REFERENCES deployment_targets(id),
    category_ids_json TEXT NOT NULL CHECK(json_valid(category_ids_json)),
    context_json TEXT NOT NULL CHECK(json_valid(context_json)),
    revision TEXT NOT NULL, updated_at TEXT NOT NULL)"""


def _trial_target(conn, trial):
    row = conn.execute("SELECT id FROM deployment_targets WHERE destination_id=?", (trial["destination_id"],)).fetchone()
    if not row:
        raise ValueError("La adaptación no tiene una conexión WordPress configurada.")
    return _target(conn, row[0])


def _selection(conn, trial, target_id):
    if _exists(conn, "adaptation_category_overrides"):
        row = conn.execute("SELECT * FROM adaptation_category_overrides WHERE trial_id=?", (trial["id"],)).fetchone()
        if row:
            if row["target_id"] != target_id:
                raise ValueError("La selección de categorías pertenece a otro destino.")
            return {"category_ids": json.loads(row["category_ids_json"]), "context": json.loads(row["context_json"]),
                    "source": "manual", "revision": row["revision"]}
    assessment_id = trial.get("snapshot", {}).get("applicability_review", {}).get("assessment_id")
    if type(assessment_id) is int and _exists(conn, "generation_assessments"):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(generation_assessments)")}
        if "category_selection_json" in columns:
            row = conn.execute("""SELECT category_selection_json FROM generation_assessments
                WHERE id=? AND article_id=? AND configuration_id=? AND status='completed'""",
                (assessment_id, trial["article_id"], trial["configuration_id"])).fetchone()
            selected = json.loads(row[0]) if row else {}
            if selected.get("selected_category_ids") and selected.get("target_id") == target_id:
                return {"category_ids": selected["selected_category_ids"], "context": selected,
                        "source": "model", "revision": _digest(selected)}
    return {"category_ids": [], "context": None, "source": "pending", "revision": ""}


def trial_category_selection(db_path, trial_id, target_id):
    trial = get_trial(db_path, trial_id)
    conn = _connect(db_path)
    try:
        if _trial_target(conn, trial)["target_id"] != target_id:
            raise ValueError("La adaptación no pertenece a este destino WordPress.")
        return _selection(conn, trial, target_id)
    finally:
        conn.close()


def get_trial_categories(db_path, trial_id):
    trial = get_trial(db_path, trial_id)
    conn = _connect(db_path)
    try:
        target = _trial_target(conn, trial)
        selected = _selection(conn, trial, target["target_id"])
        if selected["source"] == "pending" and _exists(conn, "deployment_trials"):
            row = conn.execute("SELECT payload_json FROM deployment_trials WHERE generation_trial_id=?", (trial_id,)).fetchone()
            ids = json.loads(row[0]).get("categories", []) if row else []
            if ids:
                selected = {**selected, "category_ids": ids, "source": "previous_delivery"}
        return {"trial_id": trial_id, "target_id": target["target_id"], **selected,
                "catalog": _read_catalog(conn, target)}
    finally:
        conn.close()


def save_trial_categories(db_path, trial_id, payload):
    _id(trial_id)
    if not isinstance(payload, dict) or set(payload) != {"category_ids", "catalog_revision"}:
        raise ValueError("Se requieren categorías y la revisión del catálogo.")
    trial = get_trial(db_path, trial_id)
    if trial["status"] != "completed":
        raise ValueError("Solo puede clasificar una adaptación completa.")
    conn = _connect(db_path, write=True)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            target = _trial_target(conn, trial)
            catalog = _read_catalog(conn, target)
            if not catalog["configured"]:
                raise ValueError(" ".join(catalog["blockers"]))
            if payload["catalog_revision"] != catalog["catalog_revision"]:
                raise ValueError("El catálogo cambió; recargue las categorías antes de guardar.")
            ids = payload["category_ids"]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 50:
                raise ValueError("Seleccione al menos una categoría permitida.")
            ids = sorted({_id(value) for value in ids})
            policy = catalog["policy"]
            if not set(ids) <= set(policy["allowed_category_ids"]) or policy["excluded_default_category_id"] in ids:
                raise ValueError("Seleccione únicamente categorías permitidas; la predeterminada está excluida.")
            for table, column, statuses in (("deployment_trials", "generation_trial_id", ("sending", "unknown")),
                                            ("adaptation_actions", "trial_id", ("running", "unknown"))):
                if _exists(conn, table) and conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? AND status IN (?,?)",
                                                        (trial_id, *statuses)).fetchone():
                    raise ValueError("Hay un envío en curso o pendiente de comprobar; no puede cambiar las categorías.")
            if _exists(conn, "adaptation_deletions") and conn.execute("SELECT 1 FROM adaptation_deletions WHERE trial_id=?", (trial_id,)).fetchone():
                raise KeyError(trial_id)
            context = {"target_id": target["target_id"], "catalog_revision": catalog["catalog_revision"],
                       "policy_revision": policy["policy_revision"],
                       "excluded_default_category_id": policy["excluded_default_category_id"],
                       "candidates": [category for category in catalog["categories"]
                                      if category["id"] in policy["allowed_category_ids"]]}
            revision = _digest({"category_ids": ids, "context": context})
            conn.execute(SCHEMA)
            conn.execute("""INSERT INTO adaptation_category_overrides
                (trial_id,target_id,category_ids_json,context_json,revision,updated_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(trial_id) DO UPDATE SET target_id=excluded.target_id,
                 category_ids_json=excluded.category_ids_json,context_json=excluded.context_json,
                 revision=excluded.revision,updated_at=excluded.updated_at""",
                (trial_id, target["target_id"], json.dumps(ids), json.dumps(context, ensure_ascii=False), revision, _now()))
        return get_trial_categories(db_path, trial_id)
    finally:
        conn.close()


def assert_category_state(conn, trial, target_id, selection_revision, policy_revision):
    """Reserve a delivery only if the validated local policy/selection still holds."""
    if _selection(conn, trial, target_id)["revision"] != selection_revision:
        raise ValueError("La selección de categorías cambió durante la comprobación; revise antes de enviar.")
    catalog = _read_catalog(conn, _target(conn, target_id))
    if not catalog["configured"] or catalog["policy"]["policy_revision"] != policy_revision:
        raise ValueError("La política de categorías cambió durante la comprobación; revise antes de enviar.")
