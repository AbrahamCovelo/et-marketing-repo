"""WordPress taxonomy discovery and an explicit, local publication policy.

The network operations in this module are GET requests only. Category IDs and
labels always come from the connected WordPress site, never a built-in mapping.
The default category is excluded, even if its name has been translated/renamed.
"""
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import requests

from .repository import LOCALES
from .settings import CONNECTIONS, load_credentials


CACHE_SECONDS = 3600
MAX_PAGES = 50
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS wordpress_category_catalogs (
        target_id INTEGER PRIMARY KEY REFERENCES deployment_targets(id),
        connection_key TEXT NOT NULL, base_url TEXT NOT NULL,
        categories_json TEXT NOT NULL CHECK(json_valid(categories_json)),
        wordpress_default_category_id INTEGER,
        default_status TEXT NOT NULL, catalog_revision TEXT NOT NULL,
        fetched_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wordpress_category_policies (
        target_id INTEGER PRIMARY KEY REFERENCES deployment_targets(id),
        allowed_category_ids_json TEXT NOT NULL CHECK(json_valid(allowed_category_ids_json)),
        excluded_default_category_id INTEGER NOT NULL,
        connection_key TEXT NOT NULL, base_url TEXT NOT NULL,
        catalog_revision_at_save TEXT NOT NULL,
        policy_revision TEXT NOT NULL, updated_at TEXT NOT NULL)""",
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError("La categoría debe tener un identificador entero positivo.")
    return value


def _connect(db_path, *, write=False):
    if write:
        conn = sqlite3.connect(str(db_path), timeout=15)
        conn.execute("PRAGMA foreign_keys=ON")
    else:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _target(conn, target_id):
    _id(target_id)
    row = conn.execute("""SELECT t.id,t.connection_key,d.site_url,d.locale
        FROM deployment_targets t JOIN generation_destinations d ON d.id=t.destination_id
        WHERE t.id=?""", (target_id,)).fetchone()
    if row is None:
        raise KeyError("No existe el destino WordPress.")
    key = row["connection_key"]
    if key not in CONNECTIONS:
        raise ValueError("La conexión WordPress no está admitida.")
    base_url = CONNECTIONS[key][1]
    site = urlsplit(row["site_url"])
    if (site.scheme != "https" or site.netloc != urlsplit(base_url).netloc
            or site.username or site.password or row["locale"] != LOCALES[key]):
        raise ValueError("El sitio o idioma del destino no coincide con su conexión WordPress.")
    return {"target_id": target_id, "connection_key": key, "base_url": base_url}


def _exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _initialize(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def _read_catalog(conn, target):
    result = dict(target, categories=[], catalog_revision=None, fetched_at=None, stale=True,
                  wordpress_default_category_id=None, default_status="not_checked", policy=None,
                  configured=False, blockers=[])
    if _exists(conn, "wordpress_category_catalogs"):
        row = conn.execute("SELECT * FROM wordpress_category_catalogs WHERE target_id=?",
                           (target["target_id"],)).fetchone()
        if row and all(row[key] == target[key] for key in ("connection_key", "base_url")):
            result.update(categories=json.loads(row["categories_json"]),
                          catalog_revision=row["catalog_revision"], fetched_at=row["fetched_at"],
                          wordpress_default_category_id=row["wordpress_default_category_id"],
                          default_status=row["default_status"])
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
                result["stale"] = not 0 <= age <= CACHE_SECONDS
            except (ValueError, TypeError):
                pass
    if _exists(conn, "wordpress_category_policies"):
        row = conn.execute("SELECT * FROM wordpress_category_policies WHERE target_id=?",
                           (target["target_id"],)).fetchone()
        if row and all(row[key] == target[key] for key in ("connection_key", "base_url")):
            result["policy"] = {"allowed_category_ids": json.loads(row["allowed_category_ids_json"]),
                                "excluded_default_category_id": row["excluded_default_category_id"],
                                "policy_revision": row["policy_revision"], "updated_at": row["updated_at"]}
    policy = result["policy"]
    ids = {category["id"] for category in result["categories"]}
    if not result["catalog_revision"]:
        result["blockers"].append("Actualice el catálogo de categorías de WordPress.")
    if not policy:
        result["blockers"].append("Confirme las categorías permitidas y la categoría predeterminada que se excluirá.")
    elif not set(policy["allowed_category_ids"]) <= ids or policy["excluded_default_category_id"] not in ids:
        result["blockers"].append("La política contiene categorías que ya no existen; revise la selección.")
    elif (result["wordpress_default_category_id"] is not None
          and result["wordpress_default_category_id"] != policy["excluded_default_category_id"]):
        result["blockers"].append("Cambió la categoría predeterminada de WordPress; revise la selección.")
    result["configured"] = not result["blockers"]
    return result


def get_category_catalog(db_path, target_id):
    """Read cached, public taxonomy metadata without HTTP requests or migrations."""
    conn = _connect(db_path)
    try:
        return _read_catalog(conn, _target(conn, target_id))
    finally:
        conn.close()


def _plain(value, label, limit):
    if not isinstance(value, str) or len(value) > limit * 10:
        raise ValueError(f"WordPress devolvió {label} no válido.")
    value = html.unescape(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))
    value = " ".join(value.split())
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"WordPress devolvió {label} no válido.")
    return value


def _category(value):
    if not isinstance(value, dict):
        raise ValueError("WordPress devolvió una categoría no válida.")
    category_id = _id(value.get("id"))
    parent = value.get("parent")
    if type(parent) is not int or not 0 <= parent < 2**63:
        raise ValueError("WordPress devolvió una jerarquía de categorías no válida.")
    name = _plain(value.get("name"), "un nombre de categoría", 300)
    slug = _plain(value.get("slug"), "un slug de categoría", 300)
    if not name or not slug:
        raise ValueError("WordPress devolvió una categoría sin nombre o slug.")
    return {"id": category_id, "name": name, "slug": slug,
            "description": _plain(value.get("description", ""), "una descripción", 4000), "parent": parent}


def _get(client, credentials, route, params):
    try:
        return client.request("GET", credentials.base_url + "/", params={"rest_route": route, **params,
                          "_et_check": time.time_ns()},
                          auth=(credentials.username, credentials.application_password),
                          headers={"User-Agent": "ET-Marketing/1.0", "Cache-Control": "no-cache"},
                          timeout=(10, 30), allow_redirects=False)
    except requests.RequestException:
        raise ValueError("No se pudo consultar WordPress; el catálogo anterior se conserva.") from None


def refresh_category_catalog(db_path, target_id, env_path=None, session=None):
    """Fetch every category page and try to discover the site's actual default.

    Limited WordPress accounts may not read settings. In that case the local
    policy must explicitly identify the default; no ID or translated name is
    inferred. A failed/incomplete catalogue never replaces the previous cache.
    """
    conn = _connect(db_path)
    try:
        target = _target(conn, target_id)
    finally:
        conn.close()
    credentials = load_credentials(target["connection_key"], env_path)
    if not credentials.username or not credentials.application_password:
        raise ValueError("Faltan las credenciales de WordPress para consultar categorías.")
    if credentials.base_url != target["base_url"]:
        raise ValueError("La conexión WordPress cambió; vuelva a cargar el destino.")
    client = session or requests.Session()
    categories, seen, total_pages = [], set(), None
    try:
        page = 1
        while total_pages is None or page <= total_pages:
            response = _get(client, credentials, "/wp/v2/categories", {
                "context": "view", "per_page": 100, "page": page, "hide_empty": "false",
                "orderby": "id", "order": "asc", "_fields": "id,name,slug,description,parent"})
            if response.status_code != 200:
                raise ValueError(f"WordPress rechazó la consulta de categorías (HTTP {response.status_code}).")
            try:
                rows = response.json()
            except ValueError:
                raise ValueError("WordPress no devolvió un catálogo JSON válido.") from None
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("WordPress no devolvió una página válida de categorías.")
            pages = response.headers.get("X-WP-TotalPages")
            try:
                page_count = int(pages) if pages is not None else (1 if page == 1 and len(rows) < 100 else 0)
            except (ValueError, TypeError):
                page_count = 0
            # An empty site may legitimately report zero pages.
            if page_count == 0 and not rows and page == 1 and str(pages) == "0":
                page_count = 1
            if not 1 <= page_count <= MAX_PAGES or (total_pages is not None and page_count != total_pages):
                raise ValueError("No se pudo verificar la paginación completa de categorías de WordPress.")
            total_pages = page_count
            if not rows and total_pages > 1:
                raise ValueError("WordPress devolvió un catálogo incompleto de categorías.")
            for row in rows:
                category = _category(row)
                if category["id"] in seen:
                    raise ValueError("WordPress devolvió categorías repetidas; vuelva a actualizar el catálogo.")
                seen.add(category["id"])
                categories.append(category)
            page += 1
        categories.sort(key=lambda category: category["id"])
        default_id, default_status = None, "unavailable"
        try:
            settings = _get(client, credentials, "/wp/v2/settings", {"_fields": "default_category"})
            if settings.status_code == 200:
                settings_data = settings.json()
                candidate = settings_data.get("default_category") if isinstance(settings_data, dict) else None
                if type(candidate) is int and candidate in seen:
                    default_id, default_status = candidate, "verified"
        except ValueError:
            # Taxonomy access does not require the more privileged settings scope.
            pass
    finally:
        if session is None:
            client.close()
    revision = _digest({**target, "categories": categories, "default_category_id": default_id})
    conn = _connect(db_path, write=True)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if _target(conn, target_id) != target:
                raise ValueError("El destino cambió durante la consulta; vuelva a actualizar.")
            _initialize(conn)
            conn.execute("""INSERT INTO wordpress_category_catalogs
                (target_id,connection_key,base_url,categories_json,wordpress_default_category_id,
                 default_status,catalog_revision,fetched_at) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(target_id) DO UPDATE SET connection_key=excluded.connection_key,
                 base_url=excluded.base_url,categories_json=excluded.categories_json,
                 wordpress_default_category_id=excluded.wordpress_default_category_id,
                 default_status=excluded.default_status,catalog_revision=excluded.catalog_revision,
                 fetched_at=excluded.fetched_at""", (target_id, target["connection_key"], target["base_url"],
                json.dumps(categories, ensure_ascii=False), default_id, default_status, revision, _now()))
            result = _read_catalog(conn, target)
        return result
    finally:
        conn.close()


def save_category_policy(db_path, target_id, payload):
    """Approve eligible terms and explicitly exclude the site's default term.

    excluded_default_category_id is the excluded WordPress fallback, never an automatic
    substitute for a missing classification. A missing classification blocks.
    """
    if not isinstance(payload, dict) or set(payload) != {"allowed_category_ids", "excluded_default_category_id", "catalog_revision"}:
        raise ValueError("Se requieren categorías permitidas, categoría predeterminada y revisión del catálogo.")
    allowed = payload["allowed_category_ids"]
    if not isinstance(allowed, list) or not 1 <= len(allowed) <= 50:
        raise ValueError("Seleccione entre 1 y 50 categorías permitidas.")
    allowed = sorted({_id(value) for value in allowed})
    fallback = _id(payload["excluded_default_category_id"])
    if fallback in allowed:
        raise ValueError("La categoría predeterminada debe quedar excluida de las categorías permitidas.")
    conn = _connect(db_path, write=True)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            target = _target(conn, target_id)
            current = _read_catalog(conn, target)
            if not current["catalog_revision"] or payload["catalog_revision"] != current["catalog_revision"]:
                raise ValueError("El catálogo cambió; actualícelo y confirme la selección de nuevo.")
            ids = {category["id"] for category in current["categories"]}
            if not set(allowed) <= ids or fallback not in ids:
                raise ValueError("Seleccione únicamente categorías del catálogo actual de WordPress.")
            if current["wordpress_default_category_id"] is not None and fallback != current["wordpress_default_category_id"]:
                raise ValueError("La categoría excluida debe ser la predeterminada confirmada por WordPress.")
            _initialize(conn)
            revision = _digest({**target, "allowed_category_ids": allowed, "excluded_default_category_id": fallback})
            conn.execute("""INSERT INTO wordpress_category_policies
                (target_id,allowed_category_ids_json,excluded_default_category_id,connection_key,base_url,
                 catalog_revision_at_save,policy_revision,updated_at) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(target_id) DO UPDATE SET allowed_category_ids_json=excluded.allowed_category_ids_json,
                 excluded_default_category_id=excluded.excluded_default_category_id,connection_key=excluded.connection_key,
                 base_url=excluded.base_url,catalog_revision_at_save=excluded.catalog_revision_at_save,
                 policy_revision=excluded.policy_revision,updated_at=excluded.updated_at""",
                (target_id, json.dumps(allowed), fallback, target["connection_key"], target["base_url"],
                 current["catalog_revision"], revision, _now()))
            result = _read_catalog(conn, target)
        return result
    finally:
        conn.close()


def category_context(db_path, target_id, env_path=None, session=None, refresh=False, cached_only=False):
    """Supply approved, live taxonomy candidates before any paid API request."""
    catalog = get_category_catalog(db_path, target_id)
    if not cached_only and (refresh or catalog["stale"]):
        catalog = refresh_category_catalog(db_path, target_id, env_path, session)
    if not catalog["configured"]:
        raise ValueError(" ".join(catalog["blockers"]))
    policy = catalog["policy"]
    allowed = set(policy["allowed_category_ids"])
    return {"target_id": target_id, "base_url": catalog["base_url"],
            "catalog_revision": catalog["catalog_revision"], "policy_revision": policy["policy_revision"],
            "fetched_at": catalog["fetched_at"], "excluded_default_category_id": policy["excluded_default_category_id"],
            "candidates": [category for category in catalog["categories"] if category["id"] in allowed]}


def validate_category_selection(db_path, target_id, category_ids, context=None, env_path=None,
                                session=None, refresh=True):
    """Recheck the selected terms before WordPress receives a post or an image."""
    if not isinstance(category_ids, list) or not 1 <= len(category_ids) <= 50:
        raise ValueError("El artículo necesita al menos una categoría validada antes de desplegarse.")
    ids = sorted({_id(value) for value in category_ids})
    current = category_context(db_path, target_id, env_path, session, refresh)
    candidates = {category["id"]: category for category in current["candidates"]}
    if not set(ids) <= set(candidates) or current["excluded_default_category_id"] in ids:
        raise ValueError("La adaptación contiene categorías inexistentes, predeterminadas o no permitidas.")
    if context is not None:
        if (not isinstance(context, dict) or context.get("target_id") != target_id
                or context.get("policy_revision") != current["policy_revision"]):
            raise ValueError("La política de categorías cambió desde la selección; revise la clasificación.")
        before = {category["id"]: category for category in context.get("candidates", [])
                  if isinstance(category, dict) and type(category.get("id")) is int}
        if any(before.get(category_id) != candidates[category_id] for category_id in ids):
            raise ValueError("Una categoría seleccionada cambió desde la adaptación; revise la clasificación.")
    return {"target_id": target_id, "category_ids": ids, "categories": [candidates[value] for value in ids],
            "catalog_revision": current["catalog_revision"], "policy_revision": current["policy_revision"]}
