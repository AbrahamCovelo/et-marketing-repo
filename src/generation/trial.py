"""Explicit, one-article trials, with a durable reservation before any paid call.

This service is intentionally separate from the disabled bulk executor. A run
key is never retried, including after a crash, timeout, or incomplete response.
The stored request pins the exact editorial input used for the trial.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

import requests

from .model_options import validate_model_override
from .preparation import preview_preparation
from .settings import _file_credentials


_SCHEMA = """CREATE TABLE IF NOT EXISTS generation_trials (
    id INTEGER PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    article_id INTEGER NOT NULL REFERENCES source_articles(id),
    source_version_id INTEGER NOT NULL,
    configuration_id INTEGER NOT NULL REFERENCES generation_configurations(id),
    configuration_version_id INTEGER NOT NULL,
    prompt_version_id INTEGER NOT NULL REFERENCES generation_prompt_versions(id),
    destination_id INTEGER NOT NULL REFERENCES generation_destinations(id),
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','completed','incomplete','failed','unknown')),
    request_json TEXT NOT NULL CHECK(json_valid(request_json)),
    snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
    response_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(response_json)),
    html TEXT NOT NULL DEFAULT '',
    usage_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(usage_json)),
    response_id TEXT NOT NULL DEFAULT '',
    response_model TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(source_version_id,article_id) REFERENCES article_versions(id,article_id),
    FOREIGN KEY(configuration_version_id,configuration_id)
        REFERENCES generation_configuration_versions(id,configuration_id)
)"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _record(row):
    result = dict(row)
    for field in ("request", "snapshot", "response", "usage"):
        result[field] = json.loads(result.pop(f"{field}_json"))
    return result


def _read_rows(db_path, where="", params=()):
    path = Path(db_path).resolve()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='generation_trials'").fetchone():
            return []
        records = [_record(row) for row in connection.execute(
            f"SELECT * FROM generation_trials {where} ORDER BY id DESC", params
        )]
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='adaptation_deletions'").fetchone():
            deleted = dict(connection.execute('SELECT trial_id,deleted_at FROM adaptation_deletions'))
            for record in records:
                record['deleted_at'] = deleted.get(record['id'])
        return records


def get_trial(db_path, trial_id):
    """Read a persisted trial without creating a database or changing schemas."""
    if type(trial_id) is not int or trial_id <= 0:
        raise ValueError("El identificador de la prueba debe ser un entero positivo.")
    records = _read_rows(db_path, "WHERE id=?", (trial_id,))
    if not records:
        raise KeyError("No existe la prueba solicitada.")
    if records[0].get('deleted_at'):
        raise ValueError("El contenido de esta adaptación fue eliminado de la base local.")
    return records[0]


def list_trials(db_path, article_id=None):
    """Read trial history, optionally filtered to one source article."""
    if article_id is not None:
        if type(article_id) is not int or article_id <= 0:
            raise ValueError("El artículo debe ser un identificador entero positivo.")
        return _read_rows(db_path, "WHERE article_id=?", (article_id,))
    return _read_rows(db_path)


def _same_trial(record, article_id, configuration_id, model, service_tier):
    if record.get('deleted_at'):
        raise ValueError("La adaptación fue eliminada; su clave sigue reservada y no se generará de nuevo automáticamente.")
    if (record["article_id"], record["configuration_id"], record["model"]) != (article_id, configuration_id, model):
        raise ValueError("La clave de la prueba ya pertenece a otro artículo, configuración o modelo.")
    if record["request"].get("service_tier", "default") != service_tier:
        raise ValueError("La clave de la prueba ya pertenece a otro nivel de servicio.")
    return record


def _finish(db_path, trial_id, *, status, response=None, html="", usage=None,
            response_id="", response_model="", request_id="", error=""):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("""UPDATE generation_trials SET status=?,response_json=?,html=?,usage_json=?,
            response_id=?,response_model=?,request_id=?,error=?,completed_at=? WHERE id=? AND status='running'""",
            (status, _json(response or {}), html, _json(usage or {}), response_id,
             response_model, request_id, error, _now(), trial_id))
    return get_trial(db_path, trial_id)


def _redact(value, credential):
    if isinstance(value, str):
        return value.replace(credential, "[credential redacted]")
    if isinstance(value, list):
        return [_redact(item, credential) for item in value]
    if isinstance(value, dict):
        return {str(key).replace(credential, "[credential redacted]"): _redact(item, credential)
                for key, item in value.items()}
    return value


def run_trial(db_path, article_id, configuration_id, model, run_key, *,
              env_path=None, session=None, max_output_tokens=8000, reasoning_effort=None,
              service_tier="flex"):
    """Reserve and perform at most one Responses request for this run key.

    ``session`` accepts a requests-compatible Session for offline tests. HTTP
    redirects and automatic retries are disabled for the normal owned session.
    Failures are returned as persisted records with sanitized diagnostic text.
    A running record after process interruption requires manual reconciliation;
    calling this function again cannot trigger another paid request.
    """
    for value in (article_id, configuration_id):
        if type(value) is not int or not 0 < value <= 2**63 - 1:
            raise ValueError("Selecciona un único artículo y una configuración válidos.")
    model = validate_model_override(model)
    if not model:
        raise ValueError("Selecciona un modelo concreto para la prueba.")
    if not isinstance(run_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", run_key):
        raise ValueError("La prueba necesita una clave estable de hasta 160 caracteres, sin espacios.")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 16000:
        raise ValueError("El límite de salida debe estar entre 1 y 16.000 tokens.")
    if reasoning_effort is not None and reasoning_effort not in {"minimal", "none", "low", "medium", "high"}:
        raise ValueError("Nivel de razonamiento no admitido.")
    if service_tier not in {"flex", "default"}:
        raise ValueError("Nivel de servicio no admitido para la prueba.")

    previous = _read_rows(db_path, "WHERE run_key=?", (run_key,))
    if previous:
        return _same_trial(previous[0], article_id, configuration_id, model, service_tier)

    preview = preview_preparation(db_path, [article_id], [configuration_id], model)
    example = preview["example"]
    if not example["rendered_prompt"].strip() or not example["source_context"]["content"]["clean_text"].strip():
        raise ValueError("La prueba requiere un prompt y un artículo con contenido.")
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().parents[2] / ".env"
    credential = os.environ.get("OPENAI_API_KEY", _file_credentials(path).get("OPENAI_API_KEY", "")).strip()
    if not credential:
        raise ValueError("Falta la credencial de OpenAI para ejecutar la prueba.")

    payload = {
        "model": model, "instructions": example["system_prompt"],
        "input": example["rendered_prompt"], "store": False,
        "max_output_tokens": max_output_tokens, "service_tier": service_tier,
    }
    effort = reasoning_effort or ("minimal" if model == "gpt-5-nano" or model.startswith("gpt-5-nano-") else None)
    if effort:
        payload["reasoning"] = {"effort": effort}
    snapshot = {"example": example, "item": preview["items"][0], "warnings": preview["warnings"]}

    # Fail before reserving/calling if the credential somehow appears in input.
    if credential in _json(payload) or credential in _json(snapshot):
        raise ValueError("El contenido de la prueba contiene información de autenticación.")
    with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_SCHEMA)
        connection.execute("CREATE INDEX IF NOT EXISTS generation_trials_article ON generation_trials(article_id,status)")
        existing = connection.execute("SELECT * FROM generation_trials WHERE run_key=?", (run_key,)).fetchone()
        if existing:
            return _same_trial(_record(existing), article_id, configuration_id, model, service_tier)
        result = connection.execute("""INSERT INTO generation_trials (
            run_key,article_id,source_version_id,configuration_id,configuration_version_id,
            prompt_version_id,destination_id,model,status,request_json,snapshot_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,'running',?,?,?)""", (
            run_key, article_id, example["source_version_id"], configuration_id,
            example["configuration_version_id"], example["prompt_version_id"], example["destination_id"],
            model, _json(payload), _json(snapshot), _now()))
        trial_id = result.lastrowid

    client = session if session is not None else requests.Session()
    try:
        try:
            response = client.request("POST", "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
                json=payload, timeout=(10, 600), allow_redirects=False)
        except requests.RequestException:
            return _finish(db_path, trial_id, status="unknown",
                error="No se pudo confirmar la respuesta de OpenAI. Puede haber consumo; no se reintentó la solicitud.")
        request_id = response.headers.get("x-request-id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", request_id) or credential in request_id:
            request_id = ""
        if response.status_code != 200:
            return _finish(db_path, trial_id, status="failed", request_id=request_id,
                error=f"OpenAI respondió HTTP {int(response.status_code)}. No se reintentó la solicitud.")
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Not an object")
            body = _redact(body, credential)
            # Verify it can be archived before examining partial output.
            _json(body)
        except (ValueError, TypeError):
            return _finish(db_path, trial_id, status="unknown", request_id=request_id,
                error="OpenAI devolvió una respuesta ilegible. Puede haber consumo; no se reintentó la solicitud.")

        output = body.get("output")
        text_parts = []
        if isinstance(output, list):
            for message in output:
                if not isinstance(message, dict) or message.get("type") != "message":
                    continue
                content = message.get("content")
                if isinstance(content, list):
                    text_parts.extend(part["text"] for part in content
                        if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str))
        html = "\n".join(text_parts)
        status = body.get("status")
        error = ""
        if status == "completed" and not html.strip():
            status = "failed"
            error = "OpenAI terminó sin devolver texto utilizable; se guardó la respuesta y no se reintentó."
        elif status == "incomplete":
            error = "La respuesta quedó incompleta; se conserva el texto parcial y no se reintentó."
        elif status not in {"completed", "failed"}:
            status = "unknown"
            error = "El proveedor no confirmó una respuesta final; no se reintentó."
        elif status == "failed":
            error = "El proveedor indicó un fallo; se guardó la respuesta y no se reintentó."
        return _finish(db_path, trial_id, status=status, response=body, html=html,
            usage=body.get("usage") if isinstance(body.get("usage"), dict) else {},
            response_id=body.get("id") if isinstance(body.get("id"), str) else "",
            response_model=body.get("model") if isinstance(body.get("model"), str) else "",
            request_id=request_id, error=error)
    finally:
        if session is None:
            client.close()
