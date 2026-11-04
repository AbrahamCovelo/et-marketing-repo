"""Versioned editorial settings and local plans, never generated article outputs.

This module owns its ``generation_*`` tables. It deliberately leaves the source
schema version untouched and opens a connection per operation, like the panel.
Plans pin immutable source, prompt and configuration versions. Preparing a plan
does not change any article's adaptation status and cannot execute a provider.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from src.extraction.db_manager import Database
from .preparation import PLACEHOLDERS, render_template
from .model_options import generation_options, validate_model_override


_MAX_ID = 2**63 - 1
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS generation_schema (
        version INTEGER PRIMARY KEY CHECK(version=1), seeded_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS generation_destinations (
        id INTEGER PRIMARY KEY, seed_key TEXT UNIQUE, name TEXT NOT NULL,
        site_url TEXT NOT NULL, locale TEXT NOT NULL, notes TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS generation_prompts (
        id INTEGER PRIMARY KEY, seed_key TEXT UNIQUE, current_version_id INTEGER,
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), created_at TEXT NOT NULL,
        FOREIGN KEY(current_version_id,id) REFERENCES generation_prompt_versions(id,prompt_id)
            DEFERRABLE INITIALLY DEFERRED)""",
    """CREATE TABLE IF NOT EXISTS generation_prompt_versions (
        id INTEGER PRIMARY KEY, prompt_id INTEGER NOT NULL REFERENCES generation_prompts(id),
        version INTEGER NOT NULL CHECK(version>0), name TEXT NOT NULL, description TEXT NOT NULL,
        system_prompt TEXT NOT NULL, template TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('draft','reviewed')), created_at TEXT NOT NULL,
        UNIQUE(prompt_id,version), UNIQUE(id,prompt_id))""",
    """CREATE TABLE IF NOT EXISTS generation_configurations (
        id INTEGER PRIMARY KEY, seed_key TEXT UNIQUE, current_version_id INTEGER, created_at TEXT NOT NULL,
        FOREIGN KEY(current_version_id,id) REFERENCES generation_configuration_versions(id,configuration_id)
            DEFERRABLE INITIALLY DEFERRED)""",
    """CREATE TABLE IF NOT EXISTS generation_configuration_versions (
        id INTEGER PRIMARY KEY, configuration_id INTEGER NOT NULL REFERENCES generation_configurations(id),
        version INTEGER NOT NULL CHECK(version>0), name TEXT NOT NULL,
        destination_id INTEGER NOT NULL REFERENCES generation_destinations(id),
        prompt_version_id INTEGER NOT NULL REFERENCES generation_prompt_versions(id),
        provider TEXT NOT NULL CHECK(provider IN ('openai','gemini')), model TEXT NOT NULL, notes TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), created_at TEXT NOT NULL,
        UNIQUE(configuration_id,version), UNIQUE(id,configuration_id))""",
    """CREATE TABLE IF NOT EXISTS generation_plans (
        id INTEGER PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('prepared','cancelled')),
        created_at TEXT NOT NULL, cancelled_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS generation_preparations (
        id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL REFERENCES source_articles(id),
        source_version_id INTEGER NOT NULL,
        configuration_id INTEGER NOT NULL REFERENCES generation_configurations(id),
        configuration_version_id INTEGER NOT NULL,
        prompt_version_id INTEGER NOT NULL REFERENCES generation_prompt_versions(id),
        destination_id INTEGER NOT NULL REFERENCES generation_destinations(id),
        fingerprint TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('prepared','cancelled')),
        source_snapshot_json TEXT NOT NULL CHECK(json_valid(source_snapshot_json)),
        configuration_snapshot_json TEXT NOT NULL CHECK(json_valid(configuration_snapshot_json)),
        destination_snapshot_json TEXT NOT NULL CHECK(json_valid(destination_snapshot_json)),
        prompt_snapshot_json TEXT NOT NULL CHECK(json_valid(prompt_snapshot_json)),
        warnings_json TEXT NOT NULL CHECK(json_valid(warnings_json)),
        created_at TEXT NOT NULL, cancelled_at TEXT,
        FOREIGN KEY(source_version_id,article_id) REFERENCES article_versions(id,article_id),
        FOREIGN KEY(configuration_version_id,configuration_id)
            REFERENCES generation_configuration_versions(id,configuration_id))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS generation_unique_active_preparation
        ON generation_preparations(fingerprint) WHERE status='prepared'""",
    """CREATE INDEX IF NOT EXISTS generation_preparations_article
        ON generation_preparations(article_id,status)""",
    """CREATE TABLE IF NOT EXISTS generation_plan_items (
        plan_id INTEGER NOT NULL REFERENCES generation_plans(id),
        preparation_id INTEGER NOT NULL REFERENCES generation_preparations(id),
        reused INTEGER NOT NULL CHECK(reused IN (0,1)), PRIMARY KEY(plan_id,preparation_id))""",
]
# Enforce immutable revisions even if a future caller accidentally issues UPDATE.
for _table in ("generation_prompt_versions", "generation_configuration_versions"):
    for _verb in ("UPDATE", "DELETE"):
        _SCHEMA.append(f"""CREATE TRIGGER IF NOT EXISTS {_table}_immutable_{_verb.lower()}
            BEFORE {_verb} ON {_table} BEGIN
            SELECT RAISE(ABORT, 'Editorial versions are immutable'); END""")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _MAX_ID:
        raise ValueError(f"{label} debe ser un identificador entero positivo.")
    return value


def _ids(values: Any, label: str, *, allow_empty: bool = False) -> list[int]:
    if not isinstance(values, list) or (not values and not allow_empty) or len(values) > 500:
        raise ValueError(f"{label} debe ser una lista de hasta 500 identificadores.")
    return list(dict.fromkeys(_id(value, label) for value in values))


def _text(payload: dict, key: str, *, required: bool = False, maximum: int = 100000) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{key} debe ser texto de hasta {maximum} caracteres.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key} es obligatorio.")
    return value


def _boolean(payload: dict, key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} debe ser booleano.")
    return value


def _payload(payload: Any, fields: set[str]) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Los datos deben ser un objeto JSON.")
    if set(payload) - fields:
        raise ValueError("La solicitud contiene campos no admitidos.")
    return payload


def _validate_placeholders(text: str) -> set[str]:
    # Save and preview must use the same grammar, including literal nested JSON.
    render_template(text, dict.fromkeys(PLACEHOLDERS, ""))
    return {match.group(1).strip() for match in re.finditer(r"\{\{(.*?)\}\}", text, flags=re.DOTALL)}


class GenerationRepository:
    def __init__(self, db_path: str | Path = "data/source_articles.db"):
        if str(db_path) == ":memory:":
            raise ValueError("El panel requiere una base SQLite en archivo.")
        self.db_path = Path(db_path).resolve()
        with Database(self.db_path) as db, db.connection:
            connection = db.connection
            connection.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                connection.execute(statement)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(generation_preparations)")}
            if "generation_options_json" not in columns:
                connection.execute("""ALTER TABLE generation_preparations ADD COLUMN
                    generation_options_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(generation_options_json))""")
            if not connection.execute("SELECT 1 FROM generation_schema WHERE version=1").fetchone():
                self._seed(connection)
                connection.execute("INSERT INTO generation_schema(version,seeded_at) VALUES (1,?)", (_now(),))

    def _seed(self, connection: sqlite3.Connection) -> None:
        for key, name, site_url, locale in (
            ("canadians_en", "Canadians In Spain (English)", "https://canadiansinspain.com", "en-CA"),
            ("canadians_fr", "Canadians In Spain (French)", "https://canadiansinspain.com", "fr-CA"),
            ("colombians_es", "Colombians in Spain (Español)", "https://colombianosespana.com", "es-CO"),
        ):
            now = _now()
            destination_id = connection.execute("""INSERT INTO generation_destinations
                (seed_key,name,site_url,locale,notes,enabled,created_at,updated_at)
                VALUES (?,?,?,?,?,1,?,?)""", (key, name, site_url, locale,
                "Destino inicial; confirmar URL y criterios editoriales con los socios.", now, now)).lastrowid
            prompt_id = connection.execute("""INSERT INTO generation_prompts(seed_key,created_at)
                VALUES (?,?)""", (key, now)).lastrowid
            prompt_version_id = connection.execute("""INSERT INTO generation_prompt_versions
                (prompt_id,version,name,description,system_prompt,template,status,created_at)
                VALUES (?,1,?,?,'','','draft',?)""", (prompt_id, f"Pendiente plantilla · {name}",
                "Borrador vacío pendiente de recibir y revisar la plantilla de los socios.", now)).lastrowid
            connection.execute("UPDATE generation_prompts SET current_version_id=? WHERE id=?", (prompt_version_id, prompt_id))
            configuration_id = connection.execute("""INSERT INTO generation_configurations(seed_key,created_at)
                VALUES (?,?)""", (key, now)).lastrowid
            configuration_version_id = connection.execute("""INSERT INTO generation_configuration_versions
                (configuration_id,version,name,destination_id,prompt_version_id,provider,model,notes,enabled,created_at)
                VALUES (?,1,?,?,?,'openai','',?,1,?)""", (configuration_id, name, destination_id,
                prompt_version_id, "Preparación local: pendiente de plantilla, modelo y revisión editorial.", now)).lastrowid
            connection.execute("UPDATE generation_configurations SET current_version_id=? WHERE id=?",
                               (configuration_version_id, configuration_id))

    @staticmethod
    def _destination(connection: sqlite3.Connection, destination_id: int) -> dict:
        row = connection.execute("""SELECT id,name,site_url,locale,notes,enabled,created_at,updated_at
            FROM generation_destinations WHERE id=?""", (destination_id,)).fetchone()
        if row is None:
            raise KeyError("No existe el destino.")
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    @staticmethod
    def _prompt(connection: sqlite3.Connection, prompt_id: int, version_id: int | None = None) -> dict:
        row = connection.execute("""SELECT p.id,p.archived,p.current_version_id,
            v.id AS version_id,v.version,v.name,v.description,v.system_prompt,v.template,v.status,v.created_at,
            current.version AS current_version
            FROM generation_prompts p JOIN generation_prompt_versions current ON current.id=p.current_version_id
            JOIN generation_prompt_versions v ON v.prompt_id=p.id AND v.id=COALESCE(?,p.current_version_id)
            WHERE p.id=?""", (version_id, prompt_id)).fetchone()
        if row is None:
            raise KeyError("No existe el prompt o su versión.")
        result = dict(row)
        result["archived"] = bool(result["archived"])
        return result

    def _configuration_context(self, connection: sqlite3.Connection, configuration_id: int) -> dict:
        row = connection.execute("""SELECT c.id,c.current_version_id,v.version AS current_version,
            v.name,v.destination_id,v.prompt_version_id,v.provider,v.model,v.notes,v.enabled,v.created_at,
            pv.prompt_id FROM generation_configurations c
            JOIN generation_configuration_versions v ON v.id=c.current_version_id
            JOIN generation_prompt_versions pv ON pv.id=v.prompt_version_id WHERE c.id=?""",
            (configuration_id,)).fetchone()
        if row is None:
            raise KeyError("No existe la configuración.")
        configuration = dict(row)
        configuration["enabled"] = bool(configuration["enabled"])
        prompt = self._prompt(connection, configuration["prompt_id"], configuration["prompt_version_id"])
        destination = self._destination(connection, configuration["destination_id"])
        configuration.update(prompt_name=prompt["name"], prompt_version=prompt["version"],
                             destination_name=destination["name"], locale=destination["locale"],
                             newer_prompt_available=prompt["current_version_id"] != prompt["version_id"])
        warnings = []
        if prompt["status"] != "reviewed":
            warnings.append("La plantilla está en borrador y requiere revisión editorial.")
        if not prompt["system_prompt"] or not prompt["template"]:
            warnings.append("La plantilla está incompleta; pendiente de contenido de los socios.")
        if not configuration["model"]:
            warnings.append("Falta elegir un modelo.")
        if configuration["newer_prompt_available"]:
            warnings.append("Hay una versión más reciente del prompt; esta configuración conserva la versión elegida.")
        if prompt["archived"]:
            warnings.append("El prompt está archivado.")
        if not destination["enabled"]:
            warnings.append("El destino está desactivado.")
        if not configuration["enabled"]:
            warnings.append("La configuración está desactivada.")
        configuration["warnings"] = warnings
        return {"configuration": configuration, "destination": destination, "prompt": prompt}

    def configuration_context(self, configuration_id: int) -> dict:
        _id(configuration_id, "La configuración")
        with Database(self.db_path) as db:
            return self._configuration_context(db.connection, configuration_id)

    def catalog(self) -> dict:
        with Database(self.db_path) as db:
            connection = db.connection
            destinations = [self._destination(connection, row[0]) for row in
                            connection.execute("SELECT id FROM generation_destinations ORDER BY id")]
            prompts = []
            for row in connection.execute("SELECT id FROM generation_prompts ORDER BY id"):
                prompt = self._prompt(connection, row[0])
                prompt["versions"] = [dict(version) for version in connection.execute("""SELECT
                    id,version,name,description,status,created_at FROM generation_prompt_versions
                    WHERE prompt_id=? ORDER BY version DESC""", (row[0],))]
                prompts.append(prompt)
            configurations = [self._configuration_context(connection, row[0])["configuration"] for row in
                              connection.execute("SELECT id FROM generation_configurations ORDER BY id")]
            return {"destinations": destinations, "prompts": prompts, "configurations": configurations}

    def save_destination(self, payload: dict, destination_id: int | None = None) -> dict:
        _payload(payload, {"name", "site_url", "locale", "notes", "enabled"})
        data = {key: _text(payload, key, required=key in {"name", "site_url", "locale"},
                           maximum=4000 if key == "notes" else 500) for key in
                ("name", "site_url", "locale", "notes")}
        data["enabled"] = _boolean(payload, "enabled", True)
        parsed = urlsplit(data["site_url"])
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("La URL del destino debe ser HTTP(S) y no incluir credenciales.")
        if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", data["locale"]):
            raise ValueError("El idioma debe tener formato como en-CA, fr-CA o es-CO.")
        now = _now()
        with Database(self.db_path) as db, db.connection:
            connection = db.connection
            if destination_id is None:
                destination_id = connection.execute("""INSERT INTO generation_destinations
                    (name,site_url,locale,notes,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?)""",
                    (*data.values(), now, now)).lastrowid
            else:
                _id(destination_id, "El destino")
                self._destination(connection, destination_id)
                connection.execute("""UPDATE generation_destinations
                    SET name=?,site_url=?,locale=?,notes=?,enabled=?,updated_at=? WHERE id=?""",
                    (*data.values(), now, destination_id))
            return self._destination(connection, destination_id)

    def save_prompt(self, payload: dict, prompt_id: int | None = None) -> dict:
        _payload(payload, {"name", "description", "system_prompt", "template", "status", "archived"})
        data = {key: _text(payload, key, required=key == "name", maximum=500 if key == "name" else 100000)
                for key in ("name", "description", "system_prompt", "template")}
        status = payload.get("status", "draft")
        if not isinstance(status, str) or status not in {"draft", "reviewed"}:
            raise ValueError("El estado de la plantilla debe ser draft o reviewed.")
        data["status"] = status
        archived = _boolean(payload, "archived", False)
        names = _validate_placeholders(data["system_prompt"]) | _validate_placeholders(data["template"])
        if status == "reviewed" and (not data["system_prompt"] or not data["template"] or
                                     not names.intersection({"article_text", "article_json"})):
            raise ValueError("Una plantilla revisada necesita instrucciones, plantilla y {{article_text}} o {{article_json}}.")
        with Database(self.db_path) as db, db.connection:
            connection = db.connection
            connection.execute("BEGIN IMMEDIATE")
            now = _now()
            version = 1
            previous = None
            if prompt_id is None:
                prompt_id = connection.execute("INSERT INTO generation_prompts(created_at) VALUES (?)", (now,)).lastrowid
            else:
                _id(prompt_id, "El prompt")
                previous = self._prompt(connection, prompt_id)
                version = previous["version"] + 1
            if previous is None or any(previous[key] != value for key, value in data.items()):
                version_id = connection.execute("""INSERT INTO generation_prompt_versions
                    (prompt_id,version,name,description,system_prompt,template,status,created_at)
                    VALUES (?,?,?,?,?,?,?,?)""", (prompt_id, version, *data.values(), now)).lastrowid
            else:
                version_id = previous["version_id"]
            connection.execute("UPDATE generation_prompts SET current_version_id=?,archived=? WHERE id=?",
                               (version_id, archived, prompt_id))
            return self._prompt(connection, prompt_id)

    def save_configuration(self, payload: dict, configuration_id: int | None = None) -> dict:
        _payload(payload, {"name", "destination_id", "prompt_version_id", "provider", "model", "notes", "enabled"})
        data = {"name": _text(payload, "name", required=True, maximum=500),
                "destination_id": _id(payload.get("destination_id"), "El destino"),
                "prompt_version_id": _id(payload.get("prompt_version_id"), "La versión de prompt")}
        provider = payload.get("provider", "openai")
        if not isinstance(provider, str) or provider not in {"openai", "gemini"}:
            raise ValueError("Proveedor no admitido; elija openai o gemini.")
        data.update(provider=provider, model=_text(payload, "model", maximum=200),
                    notes=_text(payload, "notes", maximum=4000), enabled=_boolean(payload, "enabled", True))
        with Database(self.db_path) as db, db.connection:
            connection = db.connection
            connection.execute("BEGIN IMMEDIATE")
            self._destination(connection, data["destination_id"])
            if not connection.execute("SELECT 1 FROM generation_prompt_versions WHERE id=?", (data["prompt_version_id"],)).fetchone():
                raise KeyError("No existe la versión de prompt.")
            now = _now()
            version = 1
            previous = None
            if configuration_id is None:
                configuration_id = connection.execute("INSERT INTO generation_configurations(created_at) VALUES (?)", (now,)).lastrowid
            else:
                _id(configuration_id, "La configuración")
                previous = self._configuration_context(connection, configuration_id)["configuration"]
                version = previous["current_version"] + 1
            if previous is None or any(previous[key] != value for key, value in data.items()):
                version_id = connection.execute("""INSERT INTO generation_configuration_versions
                    (configuration_id,version,name,destination_id,prompt_version_id,provider,model,notes,enabled,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", (configuration_id, version, *data.values(), now)).lastrowid
                connection.execute("UPDATE generation_configurations SET current_version_id=? WHERE id=?", (version_id, configuration_id))
            return self._configuration_context(connection, configuration_id)["configuration"]

    @staticmethod
    def _source(connection: sqlite3.Connection, article_id: int) -> dict:
        row = connection.execute("""SELECT a.id,a.canonical_url,a.current_version_id AS version_id,
            v.title,v.language,v.published_at,v.modified_at,v.captured_at,v.content_hash,
            v.cleaner_version,v.review_required FROM source_articles a
            JOIN article_versions v ON v.id=a.current_version_id WHERE a.id=?""", (article_id,)).fetchone()
        if row is None:
            raise KeyError("No existe el artículo o no tiene una extracción guardada.")
        source = dict(row)
        source["review_required"] = bool(source["review_required"])
        return source

    @staticmethod
    def _destination_identity(destination: dict) -> dict:
        return {key: value for key, value in destination.items() if key not in {"created_at", "updated_at"}}

    def create_plan(self, article_ids: list[int], configuration_ids: list[int], model_override=None) -> dict:
        articles = _ids(article_ids, "Los artículos")
        configurations = _ids(configuration_ids, "Las configuraciones")
        model_override = validate_model_override(model_override)
        if len(articles) * len(configurations) > 500:
            raise ValueError("Cada preparación permite como máximo 500 combinaciones de artículo y configuración.")
        with Database(self.db_path) as db, db.connection:
            connection = db.connection
            connection.execute("BEGIN IMMEDIATE")
            sources = [self._source(connection, article_id) for article_id in articles]
            contexts = [self._configuration_context(connection, configuration_id) for configuration_id in configurations]
            for context in contexts:
                if not context["configuration"]["enabled"] or not context["destination"]["enabled"] or context["prompt"]["archived"]:
                    raise ValueError("No se puede preparar una configuración o destino desactivado, ni un prompt archivado.")
                context["generation_options"] = generation_options(context["configuration"], model_override)
            now = _now()
            plan_id = connection.execute("INSERT INTO generation_plans(status,created_at) VALUES ('prepared',?)", (now,)).lastrowid
            for source in sources:
                for context in contexts:
                    configuration, destination, prompt = (context[key] for key in ("configuration", "destination", "prompt"))
                    options = context["generation_options"]
                    identity = {"source_version_id": source["version_id"],
                        "configuration_version_id": configuration["current_version_id"],
                        "destination": self._destination_identity(destination)}
                    # Preserve legacy fingerprints when using the saved model.
                    if options["model"] != configuration["model"]:
                        identity["generation_options"] = options
                    fingerprint = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
                    previous = connection.execute("SELECT id FROM generation_preparations WHERE fingerprint=? AND status='prepared'",
                                                  (fingerprint,)).fetchone()
                    if previous:
                        preparation_id = previous["id"]
                    else:
                        warnings = list(configuration["warnings"])
                        if options["model"]:
                            warnings = [warning for warning in warnings if warning != "Falta elegir un modelo."]
                        if source["review_required"]:
                            warnings.append("La extracción de origen tiene avisos que requieren revisión.")
                        preparation_id = connection.execute("""INSERT INTO generation_preparations
                            (article_id,source_version_id,configuration_id,configuration_version_id,prompt_version_id,
                             destination_id,fingerprint,status,source_snapshot_json,configuration_snapshot_json,
                             destination_snapshot_json,prompt_snapshot_json,warnings_json,created_at,generation_options_json)
                             VALUES (?,?,?,?,?,?,?,'prepared',?,?,?,?,?,?,?)""", (
                                source["id"], source["version_id"], configuration["id"], configuration["current_version_id"],
                                prompt["version_id"], destination["id"], fingerprint, _json(source),
                                _json(configuration), _json(destination), _json(prompt), _json(warnings), now, _json(options))).lastrowid
                    connection.execute("INSERT INTO generation_plan_items(plan_id,preparation_id,reused) VALUES (?,?,?)",
                                       (plan_id, preparation_id, bool(previous)))
            return self._plan_detail(connection, plan_id)

    def _preparation(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        item = dict(row)
        for key in ("source", "configuration", "destination", "prompt"):
            item[key] = json.loads(item.pop(f"{key}_snapshot_json"))
            item[f"{key}_snapshot"] = item[key]
        item["warnings"] = json.loads(item.pop("warnings_json"))
        item["generation_options"] = json.loads(item.pop("generation_options_json")) or generation_options(item["configuration"])
        item.update(provider=item["generation_options"]["provider"], model=item["generation_options"]["model"])
        article = connection.execute("SELECT current_version_id FROM source_articles WHERE id=?", (item["article_id"],)).fetchone()
        config = connection.execute("SELECT current_version_id FROM generation_configurations WHERE id=?", (item["configuration_id"],)).fetchone()
        destination = self._destination(connection, item["destination_id"])
        item.update(article_title=item["source"]["title"], configuration_name=item["configuration"]["name"],
                    configuration_version=item["configuration"]["current_version"], prompt_name=item["prompt"]["name"],
                    prompt_version=item["prompt"]["version"], destination_name=item["destination"]["name"],
                    locale=item["destination"]["locale"], source_changed=article["current_version_id"] != item["source_version_id"],
                    configuration_changed=config["current_version_id"] != item["configuration_version_id"],
                    destination_changed=self._destination_identity(destination) != self._destination_identity(item["destination"]))
        current_prompt = connection.execute("SELECT current_version_id FROM generation_prompts WHERE id=?", (item["prompt"]["id"],)).fetchone()
        item["newer_prompt_available"] = current_prompt["current_version_id"] != item["prompt_version_id"]
        item["preparation_status"] = item["status"]
        if "reused" in item:
            item["reused"] = bool(item["reused"])
        return item

    def _plan_detail(self, connection: sqlite3.Connection, plan_id: int) -> dict:
        row = connection.execute("SELECT * FROM generation_plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError("No existe la preparación.")
        plan = dict(row)
        plan["items"] = [self._preparation(connection, item) for item in connection.execute("""SELECT p.*,i.reused
            FROM generation_plan_items i JOIN generation_preparations p ON p.id=i.preparation_id
            WHERE i.plan_id=? ORDER BY p.id""", (plan_id,))]
        for item in plan["items"]:
            item["plan_id"] = plan_id
            if plan["status"] == "cancelled":
                item["status"] = "cancelled"
        plan["item_count"] = len(plan["items"])
        plan["reused_count"] = sum(item["reused"] for item in plan["items"])
        plan["created_count"] = plan["item_count"] - plan["reused_count"]
        plan["generated_count"] = 0
        plan["execution_enabled"] = False
        return plan

    def plan_detail(self, plan_id: int) -> dict:
        _id(plan_id, "La preparación")
        with Database(self.db_path) as db:
            return self._plan_detail(db.connection, plan_id)

    def list_plans(self, limit: int = 30) -> list[dict]:
        _id(limit, "El límite")
        if limit > 100:
            raise ValueError("El límite máximo es 100.")
        with Database(self.db_path) as db:
            return [dict(row) for row in db.connection.execute("""SELECT p.id,p.status,p.created_at,p.cancelled_at,
                COUNT(i.preparation_id) AS item_count,COALESCE(SUM(i.reused),0) AS reused_count,
                COUNT(i.preparation_id)-COALESCE(SUM(i.reused),0) AS created_count,0 AS generated_count
                FROM generation_plans p LEFT JOIN generation_plan_items i ON i.plan_id=p.id
                GROUP BY p.id ORDER BY p.id DESC LIMIT ?""", (limit,))]

    def cancel_plan(self, plan_id: int) -> dict:
        _id(plan_id, "La preparación")
        with Database(self.db_path) as db, db.connection:
            connection = db.connection
            connection.execute("BEGIN IMMEDIATE")
            self._plan_detail(connection, plan_id)
            now = _now()
            connection.execute("UPDATE generation_plans SET status='cancelled',cancelled_at=? WHERE id=? AND status='prepared'",
                               (now, plan_id))
            connection.execute("""UPDATE generation_preparations SET status='cancelled',cancelled_at=?
                WHERE status='prepared' AND id IN (SELECT preparation_id FROM generation_plan_items WHERE plan_id=?)
                AND NOT EXISTS (SELECT 1 FROM generation_plan_items i JOIN generation_plans p ON p.id=i.plan_id
                    WHERE i.preparation_id=generation_preparations.id AND p.status='prepared')""", (now, plan_id))
            return self._plan_detail(connection, plan_id)

    def article_history(self, article_id: int) -> dict:
        _id(article_id, "El artículo")
        with Database(self.db_path) as db:
            self._source(db.connection, article_id)
            items = [self._preparation(db.connection, row) for row in db.connection.execute(
                "SELECT * FROM generation_preparations WHERE article_id=? ORDER BY id DESC", (article_id,))]
            for item in items:
                plans = db.connection.execute("""SELECT p.id,p.status FROM generation_plan_items i
                    JOIN generation_plans p ON p.id=i.plan_id WHERE i.preparation_id=? ORDER BY p.id""", (item["id"],)).fetchall()
                item["plan_ids"] = [row["id"] for row in plans]
                active = [row["id"] for row in plans if row["status"] == "prepared"]
                item["plan_id"] = (active or item["plan_ids"])[-1]
            trials = self._trial_rows(db.connection, [article_id])
            completed = sum(item["status"] == "completed" for item in trials)
            return {"article_id": article_id, "generated_count": completed,
                    "prepared_count": sum(item["status"] == "prepared" for item in items),
                    "adaptation_status": "adapted" if completed else "not_adapted",
                    "items": items, "trials": trials,
                    "message": (f"{completed} adaptación(es) generada(s), pendientes de revisión editorial."
                                if completed else "Todavía no hubo adaptaciones.")}

    @staticmethod
    def _trial_rows(connection, article_ids: list[int], *, trial_id=None, include_html=False) -> list[dict]:
        """Read trial metadata without exposing provider requests or raw responses."""
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='generation_trials'").fetchone():
            return []
        if trial_id is None and not article_ids:
            return []
        condition = "t.id=?" if trial_id is not None else f"t.article_id IN ({','.join('?' for _ in article_ids)})"
        parameters = [trial_id] if trial_id is not None else article_ids
        fields = ",t.html" if include_html else ""
        rows = connection.execute(f"""SELECT t.id,t.article_id,t.source_version_id,t.configuration_id,
            t.configuration_version_id,t.prompt_version_id,t.destination_id,t.model,t.status,
            t.created_at,t.completed_at,t.usage_json,c.name AS configuration_name,
            c.version AS configuration_version,d.name AS destination_name,d.locale{fields}
            FROM generation_trials t
            JOIN generation_configuration_versions c ON c.id=t.configuration_version_id
            JOIN generation_destinations d ON d.id=t.destination_id
            WHERE {condition} ORDER BY t.id DESC""", parameters)
        result = []
        for row in rows:
            item = dict(row)
            raw_usage = json.loads(item.pop("usage_json") or "{}")
            item["usage"] = {key: raw_usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")
                             if isinstance(raw_usage, dict) and type(raw_usage.get(key)) is int}
            item["review_status"] = "pending" if item["status"] == "completed" else "not_ready"
            result.append(item)
        deployments = GenerationRepository._deployment_rows(connection, [item["id"] for item in result])
        for item in result:
            item["deployment"] = deployments.get(item["id"])
        return result

    @staticmethod
    def _deployment_rows(connection, trial_ids: list[int]) -> dict:
        """Expose remote draft state, constructing editor links only from fixed sites."""
        from src.deployment.settings import CONNECTIONS

        if not trial_ids:
            return {}
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('deployment_trials','deployment_targets')")}
        if tables != {"deployment_trials", "deployment_targets"}:
            return {}
        placeholders = ",".join("?" for _ in trial_ids)
        rows = connection.execute(f"""SELECT p.generation_trial_id,p.status,p.updated_at,p.result_json,
            target.connection_key FROM deployment_trials p
            JOIN generation_trials generation ON generation.id=p.generation_trial_id
            JOIN deployment_targets target ON target.id=p.target_id AND target.destination_id=generation.destination_id
            WHERE p.generation_trial_id IN ({placeholders})""", trial_ids)
        result = {}
        for row in rows:
            item = {"status": row["status"], "updated_at": row["updated_at"], "post_id": None, "editor_url": None}
            response = json.loads(row["result_json"] or "{}")
            post_id = response.get("post_id") if isinstance(response, dict) else None
            connection_info = CONNECTIONS.get(row["connection_key"])
            if (row["status"] == "draft" and type(post_id) is int and 0 < post_id <= _MAX_ID
                    and response.get("post_status") == "draft" and connection_info):
                item["post_id"] = post_id
                item["editor_url"] = f"{connection_info[1]}/wp-admin/post.php?post={post_id}&action=edit"
            result[row["generation_trial_id"]] = item
        return result

    def trial_detail(self, trial_id: int) -> dict:
        _id(trial_id, "La prueba")
        with Database(self.db_path) as db:
            rows = self._trial_rows(db.connection, [], trial_id=trial_id, include_html=True)
            if not rows:
                raise KeyError(trial_id)
            return rows[0]

    def article_summaries(self, article_ids: list[int]) -> dict[int, dict]:
        ids = _ids(article_ids, "Los artículos", allow_empty=True)
        summaries = {article_id: {"generated_count": 0, "prepared_count": 0,
                                 "adaptation_status": "not_adapted", "configurations": []} for article_id in ids}
        if not ids:
            return summaries
        placeholders = ",".join("?" for _ in ids)
        with Database(self.db_path) as db:
            for row in db.connection.execute(f"""SELECT article_id,configuration_id,configuration_snapshot_json
                FROM generation_preparations WHERE status='prepared' AND article_id IN ({placeholders}) ORDER BY id""", ids):
                summary = summaries[row["article_id"]]
                summary["prepared_count"] += 1
                config = json.loads(row["configuration_snapshot_json"])
                reference = {"id": row["configuration_id"], "name": config["name"], "version": config["current_version"]}
                if reference not in summary["configurations"]:
                    summary["configurations"].append(reference)
            for trial in self._trial_rows(db.connection, ids):
                if trial["status"] != "completed":
                    continue
                summary = summaries[trial["article_id"]]
                summary["generated_count"] += 1
                summary["adaptation_status"] = "adapted"
                reference = {"id": trial["configuration_id"], "name": trial["configuration_name"],
                             "version": trial["configuration_version"]}
                if reference not in summary["configurations"]:
                    summary["configurations"].append(reference)
        return summaries
