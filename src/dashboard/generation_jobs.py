"""Explicit panel generation jobs; durable reservations prevent duplicate spending."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

from scripts.run_automatic_trial import run_automatic_trial, _preflight
from src.deployment.repository import DeploymentRepository
from src.generation.model_catalog import validate_model_selection
from src.generation.repository import _ids
from src.generation.settings import public_status
from src.generation.trial import get_trial
from src.generation.assessment import get_assessment
from .jobs import JobBusyError
from .generation_budget import validate_quote, check_next_item


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _format_recovery_item(item):
    result = item.get("result", {})
    return (item.get("status") == "stopped" and result.get("stage") == "generation"
            and result.get("generation_status") == "completed"
            and result.get("semantic_validation", {}).get("valid") is False
            and type(result.get("trial_id")) is int and type(result.get("assessment_id")) is int)


class GenerationJobManager:
    def __init__(self, db_path, *, env_path=None, cache_path=None, runner=None,
                 workflow=run_automatic_trial, model_validator=validate_model_selection,
                 budget_validator=validate_quote, budget_guard=check_next_item):
        self.db_path = str(Path(db_path).resolve())
        self.env_path, self.cache_path = env_path, cache_path
        self.workflow, self.model_validator = workflow, model_validator
        self.budget_validator, self.budget_guard = budget_validator, budget_guard
        self.runner = runner or self._launch
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS panel_generation_runs (
                id INTEGER PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
                items_json TEXT NOT NULL CHECK(json_valid(items_json)),
                status TEXT NOT NULL CHECK(status IN ('queued','running','completed','partial','unknown')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _launch(task):
        threading.Thread(target=task, name="dashboard-generation", daemon=True).start()

    @staticmethod
    def _record(row):
        if row is None:
            raise KeyError("No existe el lote de generación.")
        result = dict(row)
        result["options"] = json.loads(result.pop("payload_json"))
        result["items"] = json.loads(result.pop("items_json"))
        result.pop("payload_hash")
        result["finished_count"] = sum(item["status"] not in {"queued", "running"} for item in result["items"])
        result["total"] = len(result["items"])
        result["can_resume_saved"] = result["status"] == "partial" and any(_format_recovery_item(item) for item in result["items"])
        return result

    def get(self, run_id):
        with closing(self._connect()) as db:
            return self._record(db.execute("SELECT * FROM panel_generation_runs WHERE id=?", (run_id,)).fetchone())

    def list(self):
        with closing(self._connect()) as db:
            return [self._record(row) for row in db.execute("SELECT * FROM panel_generation_runs ORDER BY id DESC LIMIT 20")]

    def _existing(self, key, digest):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM panel_generation_runs WHERE request_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != digest:
            raise ValueError("La clave del lote ya corresponde a otra selección. No se inició otra generación.")
        return self._record(row)

    def submit(self, body):
        fields = {"article_ids", "configuration_ids", "model", "reasoning_effort", "send_draft", "request_key"}
        optional = {'service_tier', 'budget_quote_id'}
        if not isinstance(body, dict) or not fields <= set(body) or set(body) - fields - optional:
            raise ValueError("Se requieren artículos, configuraciones, modelo, esfuerzo, opción de borrador y clave del lote.")
        articles = _ids(body["article_ids"], "Los artículos")
        configurations = _ids(body["configuration_ids"], "Las configuraciones")
        if len(articles) * len(configurations) > 500:
            raise ValueError("Seleccione como máximo 500 combinaciones por lote.")
        if type(body["send_draft"]) is not bool:
            raise ValueError("La opción de envío debe ser verdadera o falsa.")
        key = body["request_key"]
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,80}", key):
            raise ValueError("La operación necesita una clave de solicitud válida.")
        options = {**body, "article_ids": sorted(articles), "configuration_ids": sorted(configurations)}
        digest = hashlib.sha256(_json(options).encode()).hexdigest()
        existing = self._existing(key, digest)
        if existing:
            return existing
        if body.get('service_tier', 'default') not in ('default', 'flex'):
            raise ValueError('El nivel de servicio no es válido.')
        self.budget_validator(self.db_path, body)
        if not any(c["provider"] == "openai" and c["configured"] for c in public_status(self.env_path)["credentials"]):
            raise ValueError("Falta la clave local de OpenAI.")
        selected = self.model_validator(body["model"], body["reasoning_effort"], env_path=self.env_path, cache_path=self.cache_path)
        targets = DeploymentRepository(self.db_path, self.env_path).catalog()["targets"]
        items = []
        for article in articles:
            for config in configurations:
                # Resolve the configuration's WordPress target even for a local
                # adaptation, retaining destination and locale checks.
                from src.generation.applicability import review_status
                context = review_status(self.db_path, article, config)["context"]
                target = next((t for t in targets if t["destination_id"] == context["destination_id"]), None)
                if target is None:
                    raise ValueError("La configuración no tiene un destino WordPress asociado.")
                status, _ = _preflight(self.db_path, article, config, target["id"], body["send_draft"])
                if body["send_draft"] and not target["credentials"]["configured"]:
                    raise ValueError("Faltan credenciales WordPress para el borrador solicitado.")
                items.append({"article_id": article, "configuration_id": config, "target_id": target["id"],
                              "context_fingerprint": status["context_fingerprint"], "status": "queued"})
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM panel_generation_runs WHERE request_key=?", (key,)).fetchone()
            if row:
                if row["payload_hash"] != digest:
                    raise ValueError("La clave del lote corresponde a otra selección.")
                return self._record(row)
            if db.execute("SELECT 1 FROM panel_generation_runs WHERE status IN ('queued','running')").fetchone():
                raise JobBusyError("Ya hay un lote de generación en curso. Espere a que termine; no se inició otro.")
            run_id = db.execute("""INSERT INTO panel_generation_runs
                (request_key,payload_hash,payload_json,items_json,status,created_at,updated_at)
                VALUES (?,?,?,?,'queued',?,?)""", (key, digest, _json(options), _json(items), _now(), _now())).lastrowid
        try:
            self.runner(lambda: self._work(run_id, selected["reasoning_effort"] or ""))
        except Exception:
            self._save(run_id, "unknown", items)
            raise JobBusyError("No se pudo iniciar el trabajo. La solicitud quedó registrada y no se reintentó.") from None
        return self.get(run_id)

    def _save(self, run_id, status, items):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE panel_generation_runs SET status=?,items_json=?,updated_at=? WHERE id=?",
                       (status, _json(items), _now(), run_id))

    def resume_saved(self, run_id):
        """Continue format-blocked completed output; never authorizes new AI calls.

        The original selection and WordPress option are retained. Unknown remote
        attempts are deliberately excluded; this is not a generic retry button.
        """
        run = self.get(run_id)
        if run["status"] in {"queued", "running", "completed"}:
            return run
        if not run["can_resume_saved"]:
            raise ValueError("El lote no tiene resultados completos detenidos por formato que puedan recuperarse.")
        options, items = run["options"], run["items"]
        original_items = _json(items)
        for index, item in enumerate(items):
            if not _format_recovery_item(item):
                continue
            result = item["result"]
            trial = get_trial(self.db_path, result["trial_id"])
            assessment = get_assessment(self.db_path, result["assessment_id"])
            expected_key = f"panel-{run_id}-{index}"
            expected = (item["article_id"], item["configuration_id"], options["model"])
            for record, suffix in ((trial, ".generation"), (assessment, ".assessment")):
                if (record.get("article_id"), record.get("configuration_id"), record.get("model")) != expected or record.get("status") != "completed" or record.get("run_key") != expected_key + suffix:
                    raise ValueError("Los resultados guardados no corresponden a este lote; no se inició otra llamada.")
            status, _ = _preflight(self.db_path, item["article_id"], item["configuration_id"], item["target_id"], options["send_draft"])
            if status["context_fingerprint"] != item["context_fingerprint"]:
                raise ValueError("El artículo o la configuración cambió. La recuperación no generará una nueva adaptación.")
            history = item.setdefault("recovery_history", [])
            history.append({"at": _now(), "reason": item.get("reason", ""), "result": result})
            item.update(status="queued", reason="Recuperación del formato guardado, sin volver a generar.", reuse_only=True)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM panel_generation_runs WHERE id=?", (run_id,)).fetchone()
            if current["status"] in {"queued", "running", "completed"}:
                return self._record(current)
            if current["updated_at"] != run["updated_at"] or _json(json.loads(current["items_json"])) != original_items:
                raise ValueError("El estado del lote cambió. Recargue antes de recuperar el resultado.")
            if db.execute("SELECT 1 FROM panel_generation_runs WHERE status IN ('queued','running')").fetchone():
                raise JobBusyError("Ya hay otro lote activo. Espere antes de recuperar el resultado.")
            db.execute("UPDATE panel_generation_runs SET status='queued',items_json=?,updated_at=? WHERE id=?",
                       (_json(items), _now(), run_id))
        try:
            self.runner(lambda: self._work(run_id, options["reasoning_effort"] or ""))
        except Exception:
            self._save(run_id, "unknown", items)
            raise JobBusyError("No se pudo iniciar la recuperación. No se repitieron llamadas a la API.") from None
        return self.get(run_id)

    def _work(self, run_id, effort):
        with closing(self._connect()) as db, db:
            claimed = db.execute("UPDATE panel_generation_runs SET status='running',updated_at=? WHERE id=? AND status='queued'", (_now(), run_id))
            if claimed.rowcount != 1:
                return
        run = self.get(run_id)
        options, items = run["options"], run["items"]
        for index, item in enumerate(items):
            if item["status"] != "queued":
                continue
            item["status"] = "running"
            self._save(run_id, "running", items)
            try:
                status, _ = _preflight(self.db_path, item["article_id"], item["configuration_id"], item["target_id"], options["send_draft"])
                if status["context_fingerprint"] != item["context_fingerprint"]:
                    item.update(status="stopped", reason="Cambió el artículo o la configuración desde que se inició el lote.")
                    self._save(run_id, "running", items)
                    continue
                recovery = {"reuse_only": True} if item.get("reuse_only") else {}
                if not recovery:
                    try:
                        self.budget_guard(self.db_path, options, item['article_id'], item['configuration_id'])
                    except ValueError as error:
                        item.update(status='stopped', reason=str(error))
                        for remaining in items[index + 1:]:
                            if remaining['status'] == 'queued':
                                remaining.update(status='stopped', reason='Lote detenido por el control de presupuesto.')
                        self._save(run_id, 'partial', items)
                        return
                result = self.workflow(self.db_path, item["article_id"], item["configuration_id"], item["target_id"],
                    options["model"], f"panel-{run_id}-{index}", reasoning_effort=effort, service_tier=options.get('service_tier', 'default'),
                    send_draft=options["send_draft"], import_cover=options["send_draft"],
                    allow_pending_ctas=options["send_draft"], env_path=self.env_path,
                    output_dir=Path(self.db_path).parent / "adaptations", **recovery)
                # Artifacts are private filesystem paths; the panel uses the
                # adaptation API instead. Provider errors already are sanitized.
                result.pop("artifacts", None)
                item.update(status=result["status"], result=result, reason=result.get("reason", ""))
                if result.get("assessment_status") in {"unknown", "running"} or result.get("generation_status") in {"unknown", "running"} or result.get("cover_status") in {"unknown", "importing"} or result["status"] in {"unknown", "sending"}:
                    for remaining in items[index + 1:]:
                        if remaining["status"] == "queued":
                            remaining.update(status="stopped", reason="El lote se detuvo porque una operación tiene resultado incierto.")
                    self._save(run_id, "unknown", items)
                    return
            except Exception:
                # Do not expose a requests exception, an auth URL or API body.
                item.update(status="unknown", reason="La operación se interrumpió. Revise los registros antes de iniciar otra prueba; no se reintentó.")
                for remaining in items[index + 1:]:
                    if remaining["status"] == "queued":
                        remaining.update(status="stopped", reason="El lote se detuvo por una interrupción.")
                self._save(run_id, "unknown", items)
                return
            self._save(run_id, "running", items)
        complete = all(i["status"] in {"ready", "draft"} or i.get("result", {}).get("decision") == "omit" for i in items)
        self._save(run_id, "completed" if complete else "partial", items)
