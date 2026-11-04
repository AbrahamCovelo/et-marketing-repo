"""Explicit draft-only delivery of a persisted, single generation trial.

Bulk panel routes remain disabled. The delivery journal prevents a second POST,
including when a remote response is lost or the process is interrupted.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

import requests

from src.generation.trial import get_trial
from .content import prepare_wordpress_content, validate_semantic_html
from .repository import CTA_NAMES, DeploymentRepository, _id, _text
from .settings import load_credentials


_SCHEMA = """CREATE TABLE IF NOT EXISTS deployment_trials (
    id INTEGER PRIMARY KEY,
    generation_trial_id INTEGER NOT NULL UNIQUE REFERENCES generation_trials(id),
    target_id INTEGER NOT NULL REFERENCES deployment_targets(id),
    status TEXT NOT NULL CHECK(status IN ('sending','draft','failed','unknown')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    warnings_json TEXT NOT NULL CHECK(json_valid(warnings_json)),
    result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_json)),
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _record(row):
    result = dict(row)
    for field in ("payload", "warnings", "result"):
        result[field] = json.loads(result.pop(field + "_json"))
    return result


def get_deployment_trial(db_path, trial_id):
    """Read one delivery by generation trial ID, without creating a database."""
    _id(trial_id)
    path = Path(db_path).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deployment_trials'").fetchone():
            return None
        row = connection.execute("SELECT * FROM deployment_trials WHERE generation_trial_id=?", (trial_id,)).fetchone()
        return _record(row) if row else None


def _finish(db_path, trial_id, status, *, result=None, error=""):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("""UPDATE deployment_trials SET status=?,result_json=?,error=?,updated_at=?
            WHERE generation_trial_id=? AND status='sending'""",
            (status, _json(result or {}), error, _now(), trial_id))
    return get_deployment_trial(db_path, trial_id)


def _same_origin(url, base):
    if not isinstance(url, str) or len(url) > 2000 or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and parsed.netloc == urlsplit(base).netloc and not parsed.username and not parsed.password
    except ValueError:
        return False


def send_trial_draft(db_path, trial_id, *, title, featured_media, category_ids,
                     env_path=None, session=None, allow_pending_ctas=False):
    """Send at most one draft POST for this saved trial, after read-only checks.

    ``allow_pending_ctas`` preserves unresolved, known markers only in this
    explicitly requested draft. It never permits unsafe HTML or another locale.
    Preflight failures raise sanitized ValueError and can be checked again;
    after reservation all outcomes are durable and cannot be retried here.
    """
    _id(trial_id)
    previous = get_deployment_trial(db_path, trial_id)
    if previous:
        return previous
    title = _text(title, "El título", 300)
    if not title or any(c in title for c in "<>"):
        raise ValueError("El título debe ser texto sin etiquetas y no puede quedar vacío.")
    _id(featured_media)
    if not isinstance(category_ids, list) or len(category_ids) > 50:
        raise ValueError("Las categorías deben ser una lista de hasta 50 identificadores.")
    category_ids = sorted({_id(value) for value in category_ids})
    if type(allow_pending_ctas) is not bool:
        raise ValueError("La autorización para conservar CTA pendientes debe ser booleana.")
    trial = get_trial(db_path, trial_id)
    if trial["status"] != "completed":
        raise ValueError("Solo se puede enviar una prueba de generación completada.")
    repository = DeploymentRepository(db_path, env_path=env_path)
    targets = [target for target in repository.catalog()["targets"] if target["destination_id"] == trial["destination_id"]]
    if len(targets) != 1:
        raise ValueError("La prueba no tiene una conexión WordPress única.")
    target = targets[0]
    if target["connection_key"] == "canadians_fr" or target["language_mode"] != "site_default":
        raise ValueError("La integración del idioma sigue pendiente.")
    # The destination is mutable; validate it independently of draft categories.
    with closing(sqlite3.connect(db_path)) as connection:
        destination = connection.execute("SELECT site_url,locale,enabled FROM generation_destinations WHERE id=?",
                                         (trial["destination_id"],)).fetchone()
    if not destination or not destination[2] or destination[1] != target["locale"] or not _same_origin(destination[0], target["base_url"]):
        raise ValueError("El sitio o idioma del destino no coincide con la conexión WordPress.")

    validation = validate_semantic_html(trial["html"])
    if not validation["valid"]:
        raise ValueError("El HTML guardado no supera la validación semántica.")
    if set(validation["cta_markers"]) - set(CTA_NAMES[target["locale"]]):
        raise ValueError("El HTML contiene marcadores CTA de otro idioma.")
    prepared = prepare_wordpress_content(trial["html"], target["cta_links"])
    warnings = ["Borrador de prueba: pendiente de revisión editorial antes de publicar."]
    if prepared["ready"]:
        content = prepared["content"]
    elif allow_pending_ctas:
        content = trial["html"]
        warnings.append("Se conservan los marcadores CTA sin resolver para revisión dentro del borrador.")
    else:
        raise ValueError("Falta configurar los enlaces CTA antes del envío.")
    credentials = load_credentials(target["connection_key"], env_path)
    if not credentials.username or not credentials.application_password:
        raise ValueError("Faltan credenciales para WordPress.")
    base = target["base_url"]
    payload = {"status": "draft", "title": title, "content": content,
               "featured_media": featured_media, "categories": category_ids,
               "slug": f"et-trial-{trial_id}-article-{trial['article_id']}"}
    # Never archive credentials even if a caller accidentally included them.
    secrets = [credentials.application_password, credentials.application_password.replace(" ", "")]
    if any(secret and secret in _json(payload) for secret in secrets):
        raise ValueError("El borrador contiene información de autenticación.")

    client = session if session is not None else requests.Session()
    def request(method, route, *, params=None, body=None):
        return client.request(method, base + "/",
            params={"rest_route": "/wp/v2/" + route, **({"_et_check": str(time.time_ns())} if method == "GET" else {}), **(params or {})},
            auth=(credentials.username, credentials.application_password),
            headers={"User-Agent": "ET-Marketing/1.0", "Accept": "application/json", "Cache-Control": "no-cache"},
            json=body, timeout=(10, 60), allow_redirects=False)

    def checked_get(route, params=None):
        try:
            response = request("GET", route, params=params)
        except requests.RequestException:
            raise ValueError("No se pudo comprobar WordPress; no se envió el borrador.") from None
        if response.status_code != 200:
            raise ValueError(f"La comprobación de WordPress respondió HTTP {int(response.status_code)}; no se envió el borrador.")
        try:
            return response.json()
        except ValueError:
            raise ValueError("WordPress devolvió una comprobación ilegible; no se envió el borrador.") from None

    try:
        user = checked_get("users/me", {"context": "edit"})
        if (not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0
                or not isinstance(user.get("capabilities"), dict) or user["capabilities"].get("edit_posts") is not True):
            raise ValueError("WordPress no confirmó permisos para crear borradores.")
        media = checked_get(f"media/{featured_media}", {"context": "edit"})
        if (not isinstance(media, dict) or type(media.get("id")) is not int or media["id"] != featured_media
                or not isinstance(media.get("mime_type"), str) or not media["mime_type"].startswith("image/")
                or not _same_origin(media.get("source_url"), base)):
            raise ValueError("WordPress no confirmó una imagen válida de este sitio.")
        if category_ids:
            categories = checked_get("categories", {"include": ",".join(map(str, category_ids)), "per_page": 100})
            if (not isinstance(categories, list) or any(not isinstance(c, dict) or type(c.get("id")) is not int for c in categories)
                    or {c["id"] for c in categories} != set(category_ids)):
                raise ValueError("WordPress no confirmó todas las categorías solicitadas.")
        with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_SCHEMA)
            existing = connection.execute("SELECT * FROM deployment_trials WHERE generation_trial_id=?", (trial_id,)).fetchone()
            if existing:
                return _record(existing)
            now = _now()
            connection.execute("""INSERT INTO deployment_trials
                (generation_trial_id,target_id,status,payload_json,warnings_json,created_at,updated_at)
                VALUES (?,?,'sending',?,?,?,?)""", (trial_id,target["id"],_json(payload),_json(warnings),now,now))
        try:
            response = request("POST", "posts", body=payload)
        except requests.RequestException:
            return _finish(db_path, trial_id, "unknown", error="No se pudo confirmar el envío. Puede existir un borrador; no se reintentó.")
        if response.status_code not in (200, 201):
            # Server errors could have happened after saving; retain uncertainty.
            status = "unknown" if response.status_code >= 500 else "failed"
            return _finish(db_path, trial_id, status,
                           error=f"WordPress respondió HTTP {int(response.status_code)} al envío. No se reintentó.")
        try:
            data = response.json()
        except ValueError:
            return _finish(db_path, trial_id, "unknown", error="WordPress devolvió una respuesta ilegible al envío. No se reintentó.")
        result = {}
        if isinstance(data, dict) and type(data.get("id")) is int and data["id"] > 0:
            result = {"post_id": data["id"], "edit_url": f"{base}/wp-admin/post.php?post={data['id']}&action=edit"}
            if isinstance(data.get("status"), str) and data["status"] in {"draft", "publish", "private", "pending", "future", "trash"}:
                result["post_status"] = data["status"]
            if type(data.get("featured_media")) is int:
                result["featured_media"] = data["featured_media"]
            link = data.get("link")
            if _same_origin(link, base) and not any(secret and secret in link for secret in secrets):
                result["link"] = link
        if not result or result.get("post_status") != "draft" or result.get("featured_media") != featured_media:
            return _finish(db_path, trial_id, "unknown", result=result,
                           error="WordPress no confirmó un borrador con la imagen solicitada; requiere comprobación manual, sin reintento.")
        return _finish(db_path, trial_id, "draft", result=result)
    finally:
        if session is None:
            client.close()
