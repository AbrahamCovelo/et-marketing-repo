"""Read-only model discovery: account availability plus official model docs.

Only GET requests are made. No Responses request or capability probe is sent.
The Models API does not report reasoning efforts. Those are parsed conservatively
from each model's public Markdown reference; missing information stays unknown.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

import requests

from .model_options import validate_model_override
from .settings import _file_credentials


MODELS_URL = "https://api.openai.com/v1/models"
MODELS_REFERENCE = "https://developers.openai.com/api/reference/resources/models/methods/list"
DOCS_ROOT = "https://developers.openai.com/api/docs/models/"
LIST_TTL = 3600
DOCS_TTL = 86400
UNKNOWN_DOCS_TTL = 300
_LOCK = threading.RLock()
_MAX_DOCUMENT = 250_000


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _credential(env_path):
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().parents[2] / ".env"
    return os.environ.get("OPENAI_API_KEY", _file_credentials(path).get("OPENAI_API_KEY", "")).strip()


def _cache_path(cache_path):
    return Path(cache_path) if cache_path is not None else Path(__file__).resolve().parents[2] / "data" / "model-catalog.json"


def _read_cache(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) and result.get("version") == 1 else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _cached_record(path, key):
    value = _read_cache(path).get(key, {})
    return value if isinstance(value, dict) else {}


def _store(path, key, value):
    with _LOCK:
        data = _read_cache(path)
        data.update(version=1)
        data[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


def _fresh(record, ttl):
    stamp = record.get("fetched_at", 0) if isinstance(record, dict) else 0
    return isinstance(stamp, (int, float)) and 0 <= time.time() - stamp < ttl


def _catalog_result(record=None, *, cached=False, stale=False, message="", status="available"):
    return {
        "models": (record or {}).get("models", []),
        "checked_at": (record or {}).get("checked_at"),
        "source_url": MODELS_REFERENCE,
        "availability_checked": bool(record),
        "cached": cached, "stale": stale, "status": status,
        "message": message or "Lista de la cuenta consultada sin generar contenido. Los esfuerzos se consultan al escoger un modelo.",
        "dynamic": True,
    }


def get_catalog(*, env_path=None, cache_path=None, refresh=False, session=None):
    """List available GPT text candidates; selected capabilities are checked separately.

Numeric GPT IDs distinguish the text model line from image/audio/realtime
products, without enumerating model IDs. Actual Responses compatibility is
verified from the selected model's documentation, not inferred from its name.
"""
    key = _credential(env_path)
    if not key:
        return _catalog_result(status="unconfigured", message="Configure la clave de OpenAI para consultar los modelos disponibles.")
    path = _cache_path(cache_path)
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    with _LOCK:
        cached = _cached_record(path, "catalog")
    if cached.get("account_fingerprint") != fingerprint or not isinstance(cached.get("models"), list):
        cached = {}
    if not refresh and cached and _fresh(cached, LIST_TTL):
        return _catalog_result(cached, cached=True)
    client = session or requests
    try:
        response = client.get(MODELS_URL, headers={"Authorization": "Bearer " + key,
                              "Accept": "application/json"}, timeout=(10, 25), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("http")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("schema")
        today = datetime.now(timezone.utc).date().isoformat()
        models = {}
        for model in payload["data"]:
            if not isinstance(model, dict):
                continue
            identifier = model.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"gpt-\d[A-Za-z0-9._-]*", identifier):
                continue
            shutdown = model.get("shutdown_date")
            if isinstance(shutdown, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", shutdown) and shutdown <= today:
                continue
            models[identifier] = {"id": identifier, "label": identifier,
                                  "created": model.get("created"), "shutdown_date": shutdown,
                                  "reasoning_efforts": None, "capabilities_status": "unchecked"}
        record = {"models": sorted(models.values(), key=lambda item: item["id"], reverse=True),
                  "checked_at": _now(), "fetched_at": time.time(), "account_fingerprint": fingerprint}
        _store(path, "catalog", record)
        return _catalog_result(record)
    except (requests.RequestException, ValueError, TypeError):
        # Error bodies, URLs and exception strings can contain credentials.
        if cached:
            return _catalog_result(cached, cached=True, stale=True, status="stale",
                                   message="No se pudo actualizar OpenAI. Se muestra la última lista guardada; la disponibilidad puede haber cambiado.")
        return _catalog_result(status="unavailable", message="No se pudo consultar la lista de OpenAI. Revise la clave o la conexión y vuelva a actualizar.")


def _section(markdown, heading):
    match = re.search(r"^## " + re.escape(heading) + r"\s*\n(.*?)(?=^## |\Z)", markdown, re.M | re.S)
    return match[1] if match else None


def parse_capabilities(model_id, markdown, source_url):
    """Parse positive documentation evidence; never infer effort by family name."""
    identity = re.search(r"^Model ID:\s*`([^`]+)`", markdown, re.M)
    snapshots = _section(markdown, "Snapshots") or ""
    listed = re.findall(r"`([^`]+)`", snapshots)
    if not identity or model_id not in {identity[1], *listed}:
        raise ValueError("La documentación no identifica el modelo solicitado.")
    header = markdown.split("## Model details", 1)[0]
    effort_clause = re.search(r"`reasoning\.effort`\s+supports\s+(.*?)(?:\.(?:\s|$)|\n\n)", header, re.S | re.I)
    efforts = None
    default_effort = None
    reasoning_supported = None
    if effort_clause:
        efforts = re.findall(r"`([a-z][a-z0-9_-]*)`", effort_clause[1])
        efforts = list(dict.fromkeys(efforts)) or None
        reasoning_supported = True if efforts else None
        default = re.search(r"`([a-z][a-z0-9_-]*)`\s*\(default\)", effort_clause[1])
        default_effort = default[1] if default else None
    elif re.search(r"non[- ]reasoning|without a reasoning step|does not support reasoning", header, re.I):
        efforts, reasoning_supported = [], False
    details = _section(markdown, "Model details") or ""
    output = re.search(r"^- Output modalities:\s*([^\n]+)", details, re.M)
    text_output = "text" in output[1].lower().split(", ") if output else None
    endpoints = _section(markdown, "Endpoints") or ""
    responses = re.search(r"^\|\s*Responses\s*\|[^\n]+\|\s*(Supported|Not supported)\s*\|", endpoints, re.M | re.I)
    features, tools = _section(markdown, "Supported features"), _section(markdown, "Supported tools")
    unsupported = _section(markdown, "Unsupported features") or ""
    def supported(name, section):
        if re.search(r"^- " + re.escape(name) + r"\s*$", unsupported, re.M):
            return False
        if section is None:
            return None
        return bool(re.search(r"^- " + re.escape(name) + r"\s*$", section, re.M))
    values = {"text_output": text_output,
              "responses": responses[1].lower() == "supported" if responses else None,
              "structured_outputs": supported("structured_outputs", features),
              "web_search": supported("web_search", tools if tools is not None else features)}
    status_match = re.search(r"^Status:\s*([^\n]+)", markdown, re.M)
    return {"id": model_id, "status": "verified", "source_url": source_url, "checked_at": _now(),
            "reasoning_efforts": efforts, "default_reasoning_effort": default_effort,
            "reasoning_supported": reasoning_supported, **values,
            "automatic_supported": not any(value is False for value in values.values()),
            "model_status": status_match[1].strip() if status_match else None,
            "warning": "" if efforts is not None else "La documentación no enumera los esfuerzos. Use el valor predeterminado del proveedor."}


def _unknown(model_id):
    return {"id": model_id, "status": "unknown", "reasoning_efforts": None,
            "default_reasoning_effort": None, "reasoning_supported": None,
            "responses": None, "text_output": None, "structured_outputs": None, "web_search": None,
            "automatic_supported": True, "model_status": None, "source_url": DOCS_ROOT + model_id,
            "checked_at": None, "warning": "No se pudieron verificar las capacidades. Solo se ofrece el esfuerzo predeterminado del proveedor."}


def get_model_capabilities(model_id, *, cache_path=None, refresh=False, session=None):
    model_id = validate_model_override(model_id)
    if not model_id or not re.fullmatch(r"gpt-[A-Za-z0-9][A-Za-z0-9._-]*", model_id):
        raise ValueError("El modelo debe ser un identificador GPT válido.")
    path = _cache_path(cache_path)
    cache_key = "model:" + model_id
    with _LOCK:
        cached = _cached_record(path, cache_key)
    if not isinstance(cached.get("capabilities"), dict) or cached["capabilities"].get("id") != model_id:
        cached = {}
    ttl = UNKNOWN_DOCS_TTL if cached.get("capabilities", {}).get("status") == "unknown" else DOCS_TTL
    if not refresh and cached and _fresh(cached, ttl):
        return {**cached["capabilities"], "cached": True, "stale": False}
    client = session or requests
    candidates = [model_id]
    # A snapshot may use the parent's page only if that page explicitly lists
    # the exact snapshot. Removing the date is only a URL lookup strategy.
    base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model_id)
    if base != model_id:
        candidates.append(base)
    for candidate in candidates:
        source_url = DOCS_ROOT + candidate
        try:
            response = client.get(source_url + ".md", headers={"Accept": "text/markdown"},
                                  timeout=(5, 12), allow_redirects=False)
            if response.status_code != 200 or len(response.content) > _MAX_DOCUMENT:
                continue
            capabilities = parse_capabilities(model_id, response.content.decode("utf-8"), source_url)
            _store(path, cache_key, {"fetched_at": time.time(), "capabilities": capabilities})
            return {**capabilities, "cached": False, "stale": False}
        except (requests.RequestException, ValueError, UnicodeError):
            continue
    if cached and cached["capabilities"].get("status") == "verified":
        return {**cached["capabilities"], "cached": True, "stale": True,
                "warning": "No se pudo actualizar la documentación; se muestra la última comprobación guardada."}
    capabilities = _unknown(model_id)
    _store(path, cache_key, {"fetched_at": time.time(), "capabilities": capabilities})
    return {**capabilities, "cached": False, "stale": False}


def validate_model_selection(model_id, reasoning_effort=None, *, env_path=None, cache_path=None, session=None):
    """Read-only execution preflight; unknown capabilities allow provider defaults.

Returns the selected values plus capabilities. Callers must omit ``reasoning``
from the paid request when ``reasoning_effort`` is None, rather than sending an
invented effort. Explicit efforts require documentation evidence.
"""
    model_id = validate_model_override(model_id)
    catalog = get_catalog(env_path=env_path, cache_path=cache_path, session=session)
    if catalog["status"] not in {"available", "stale"}:
        raise ValueError(catalog["message"])
    if model_id not in {item["id"] for item in catalog["models"]}:
        raise ValueError("El modelo no aparece en la lista disponible de esta cuenta. Actualice los modelos.")
    capabilities = get_model_capabilities(model_id, cache_path=cache_path, session=session)
    if not capabilities["automatic_supported"]:
        raise ValueError("Este modelo no admite las funciones necesarias para la adaptación automática (texto, Responses, búsqueda web y salida estructurada).")
    if reasoning_effort == "":
        reasoning_effort = None
    if reasoning_effort is not None and (not isinstance(reasoning_effort, str)
            or reasoning_effort not in (capabilities["reasoning_efforts"] or [])):
        raise ValueError("Ese esfuerzo no está documentado para el modelo. Escoja uno de la lista o el predeterminado del proveedor.")
    return {"model": model_id, "reasoning_effort": reasoning_effort,
            "capabilities": capabilities, "catalog_stale": catalog["stale"]}
