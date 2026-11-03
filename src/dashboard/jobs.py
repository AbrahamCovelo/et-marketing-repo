"""Serial background jobs for the local data panel.

The panel keeps live progress in memory. Every actual operation also has a
durable crawl_runs entry, and article/fetch history uses the extraction store.
Source dates are checked after downloading the current page, never against a
possibly stale date in SQLite.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import threading
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from src.extraction.cleaner import extract_html
from src.extraction.db_manager import Database
from src.extraction.scraper import HttpClient, discover_guides, normalize_url
from .repository import source_calendar_date, validate_date_range


class JobBusyError(RuntimeError):
    """The panel already has one operation in progress."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} debe ser un entero entre {minimum} y {maximum}.")
    return value


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} debe estar entre {minimum} y {maximum}.")
    return float(value)


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} debe ser verdadero o falso.")
    return value


def _article_url(value: Any) -> str:
    # The UI accepts article URLs, not local paths or arbitrary query endpoints.
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Cada URL debe ser una dirección HTTP/HTTPS del sitio fuente.")
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc or "\\" in value:
        raise ValueError("Introduzca URL completas de artículos de movingtospain.com.")
    url = normalize_url(value)
    parts = urlsplit(url)
    if parts.query or parts.path == "/" or re.fullmatch(r"/guides/(?:page/\d+/)?", parts.path):
        raise ValueError("La extracción directa necesita URL de artículos sin parámetros de consulta.")
    return url


class JobManager:
    """One daemon job at a time, with injectable HTTP/extraction for offline tests."""

    def __init__(self, db_path: str | Path, *, client_factory: Callable = HttpClient,
                 extractor: Callable = extract_html, discoverer: Callable = discover_guides,
                 runner: Callable[[Callable[[], None]], None] | None = None,
                 max_jobs: int = 30):
        if str(db_path) == ":memory:":
            raise ValueError("Los trabajos necesitan una base SQLite en archivo.")
        self.db_path = Path(db_path).resolve()
        self.client_factory = client_factory
        self.extractor = extractor
        self.discoverer = discoverer
        self.runner = runner or self._launch
        self.max_jobs = _integer(max_jobs, "max_jobs", 1, 100)
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._active: str | None = None

    @staticmethod
    def _launch(task: Callable[[], None]) -> None:
        threading.Thread(target=task, name="dashboard-extraction", daemon=True).start()

    def _ids(self, value: Any) -> list[int]:
        if not isinstance(value, list) or not value or len(value) > 5000:
            raise ValueError("Seleccione entre 1 y 5000 artículos.")
        ids = list(dict.fromkeys(_integer(item, "ID de artículo", 1, 2 ** 63 - 1) for item in value))
        with Database(self.db_path) as db:
            existing = {row[0] for row in db.connection.execute("SELECT id FROM source_articles WHERE current_version_id IS NOT NULL")}
        missing = set(ids) - existing
        if missing:
            raise ValueError("La selección contiene artículos que no existen.")
        return ids

    def _validate(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("La operación debe enviarse como un objeto JSON.")
        allowed = {"action", "scope", "mode", "urls", "article_ids", "max_pages", "all_pages", "limit",
                   "delay", "timeout", "date_field", "date_from", "date_to"}
        if set(payload) - allowed:
            raise ValueError("La operación contiene parámetros desconocidos.")
        action = payload.get("action")
        if not isinstance(action, str) or action not in {"discover", "scrape", "reclean"}:
            raise ValueError("Acción no admitida: use discover, scrape o reclean.")
        config = {"action": action}
        if action == "reclean":
            scope = payload.get("scope", "selected")
            if not isinstance(scope, str) or scope not in {"all", "selected"}:
                raise ValueError("La limpieza admite todos los artículos o una selección.")
            config["scope"] = scope
            if any(payload.get(field) for field in ("date_from", "date_to")):
                raise ValueError("La limpieza local no usa filtros de fecha; seleccione los artículos en la lista.")
            if scope == "selected":
                config["article_ids"] = self._ids(payload.get("article_ids"))
            return config
        config.update({
            "delay": _number(payload.get("delay", 1.0), "Pausa", 0.25, 30.0),
            "timeout": _number(payload.get("timeout", 30.0), "Tiempo de espera", 1.0, 120.0),
            "max_pages": _integer(payload.get("max_pages", 3), "Páginas", 1, 500),
            "all_pages": _boolean(payload.get("all_pages", False), "Todas las páginas"),
        })
        if payload.get("limit") is not None:
            config["limit"] = _integer(payload["limit"], "Límite de artículos", 1, 5000)
        if action == "discover":
            if any(payload.get(field) for field in ("date_from", "date_to")):
                raise ValueError("El índice no proporciona fechas; use la extracción para filtrar por fecha.")
            return config
        scope = payload.get("scope", "catalog")
        if not isinstance(scope, str) or scope not in {"catalog", "urls", "selected"}:
            raise ValueError("El alcance debe ser catalog, urls o selected.")
        mode = payload.get("mode", "new")
        if not isinstance(mode, str) or mode not in {"new", "refresh"}:
            raise ValueError("El modo debe ser new o refresh.")
        date_field = payload.get("date_field", "published_at")
        if not isinstance(date_field, str) or date_field not in {"published_at", "modified_at"}:
            raise ValueError("Filtre la fuente por publicación o última modificación.")
        date_from, date_to = validate_date_range(payload.get("date_from"), payload.get("date_to"))
        config.update(scope=scope, mode=mode, date_field=date_field, date_from=date_from, date_to=date_to)
        if scope == "urls":
            urls = payload.get("urls")
            if not isinstance(urls, list) or not urls or len(urls) > 5000:
                raise ValueError("Introduzca entre 1 y 5000 URL de artículos.")
            config["urls"] = list(dict.fromkeys(_article_url(url) for url in urls))
        elif scope == "selected":
            config["article_ids"] = self._ids(payload.get("article_ids"))
        return config

    def submit(self, payload: Any) -> dict:
        config = self._validate(payload)
        with self._lock:
            if self._active is not None:
                raise JobBusyError("Ya hay una operación en curso. Espere a que termine.")
            job_id = uuid4().hex
            self._jobs[job_id] = {
                "id": job_id, "action": config["action"], "status": "queued", "stage": "queued",
                "message": "Operación pendiente.", "current_url": None,
                "created_at": _now(), "started_at": None, "finished_at": None, "run_id": None,
                "progress": {"total": 0, "processed": 0, "success": 0, "skipped": 0,
                             "failed": 0, "out_of_range": 0, "missing_date": 0},
                "errors": [], "result": {}, "config": deepcopy(config),
            }
            self._active = job_id
            while len(self._jobs) > self.max_jobs:
                self._jobs.popitem(last=False)
        try:
            self.runner(lambda: self._run(job_id, config))
        except Exception as exc:
            self._update(job_id, status="failed", stage="finished", finished_at=_now(), message=str(exc))
            with self._lock:
                self._active = None
            raise
        return self.get_job(job_id)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return deepcopy(list(reversed(self._jobs.values())))

    def get_job(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError("La operación no existe o ya salió del historial reciente.")
            return deepcopy(self._jobs[job_id])

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            self._jobs[job_id].update(deepcopy(values))

    def _tick(self, job_id: str, **increments) -> None:
        with self._lock:
            for key, value in increments.items():
                self._jobs[job_id]["progress"][key] += value

    def _error(self, job_id: str, url: str | None, error: Exception) -> None:
        with self._lock:
            self._jobs[job_id]["errors"].append({"url": url, "error": str(error)})

    def _selected(self, db: Database, config: dict) -> list[dict]:
        rows = db.list_articles()
        if config["scope"] == "all":
            return rows
        by_id = {row["id"]: row for row in rows}
        return [by_id[article_id] for article_id in config["article_ids"]]

    def _discover(self, job_id: str, db: Database, run_id: int, client, config: dict) -> list[str]:
        self._update(job_id, stage="discovering", message="Recorriendo el índice de guías…")
        manager = self

        class IndexClient:
            def get(self, url):
                manager._update(job_id, current_url=url)
                try:
                    response = client.get(url)
                    db.record_fetch(run_id, url, status_code=response.status_code,
                                    final_url=response.url, resource="index", disposition="discovered")
                    return response
                except Exception as exc:
                    db.record_fetch(run_id, url, status_code=getattr(exc, "status_code", None),
                                    error=str(exc), resource="index")
                    raise

        urls = self.discoverer(max_pages=None if config["all_pages"] else config["max_pages"], client=IndexClient())
        urls = list(dict.fromkeys(_article_url(url) for url in urls))
        return urls[:config.get("limit", len(urls))]

    def _scrape(self, job_id: str, db: Database, run_id: int, client, config: dict) -> None:
        if config["scope"] == "catalog":
            urls = self._discover(job_id, db, run_id, client, config)
        elif config["scope"] == "selected":
            urls = [_article_url(row["canonical_url"]) for row in self._selected(db, config)]
        else:
            urls = config["urls"]
        urls = urls[:config.get("limit", len(urls))]
        self._tick(job_id, total=len(urls))
        self._update(job_id, stage="extracting", message=f"Procesando {len(urls)} artículos.")
        for url in urls:
            self._update(job_id, current_url=url)
            status_code = None
            try:
                if config["mode"] == "new" and db.has_article(url):
                    self._tick(job_id, skipped=1)
                    continue
                response = client.get(url)
                status_code = response.status_code
                result = self.extractor(response.html, response.url)
                result.canonical_url = _article_url(result.canonical_url)
                if config["date_from"] or config["date_to"]:
                    value = getattr(result, config["date_field"])
                    day = source_calendar_date(value, config["date_field"])
                    if day is None:
                        self._tick(job_id, skipped=1, missing_date=1)
                        db.record_fetch(run_id, url, status_code=status_code, final_url=response.url,
                                        disposition="missing_date", date_field=config["date_field"])
                        continue
                    if (config["date_from"] and day < config["date_from"]) or (config["date_to"] and day > config["date_to"]):
                        self._tick(job_id, skipped=1, out_of_range=1)
                        db.record_fetch(run_id, url, status_code=status_code, final_url=response.url,
                                        disposition="outside_date_range", source_date=value,
                                        date_field=config["date_field"])
                        continue
                # A newly encountered alias may point to an article already stored.
                if config["mode"] == "new" and db.has_article(result.canonical_url):
                    self._tick(job_id, skipped=1)
                    existing = db.connection.execute(
                        "SELECT id,current_version_id FROM source_articles WHERE canonical_url=?",
                        (result.canonical_url,),
                    ).fetchone()
                    db.record_fetch(run_id, url, status_code=status_code, final_url=response.url,
                                    article_id=existing["id"] if existing else None,
                                    version_id=existing["current_version_id"] if existing else None,
                                    disposition="existing_canonical")
                    continue
                db.store_article(result, raw_html=response.html, requested_url=url,
                                 status_code=status_code, run_id=run_id)
                self._tick(job_id, success=1)
            except Exception as exc:
                self._tick(job_id, failed=1)
                self._error(job_id, url, exc)
                db.record_fetch(run_id, url, status_code=getattr(exc, "status_code", None) or status_code,
                                error=str(exc))
            finally:
                self._tick(job_id, processed=1)

    def _reclean(self, job_id: str, db: Database, run_id: int, config: dict) -> None:
        articles = self._selected(db, config)
        self._tick(job_id, total=len(articles))
        self._update(job_id, stage="cleaning", message=f"Limpiando {len(articles)} respaldos locales, sin red.")
        for article in articles:
            url = article["canonical_url"]
            self._update(job_id, current_url=url)
            try:
                snapshot = db.get_snapshot(article["id"])
                result = self.extractor(snapshot["html"], snapshot["url"])
                result.canonical_url = _article_url(result.canonical_url)
                db.store_article(result, raw_html=snapshot["html"], requested_url=url,
                                 status_code=None, run_id=run_id)
                self._tick(job_id, success=1)
            except Exception as exc:
                self._tick(job_id, failed=1)
                self._error(job_id, url, exc)
                db.record_fetch(run_id, url, error=str(exc))
            finally:
                self._tick(job_id, processed=1)

    def _run(self, job_id: str, config: dict) -> None:
        db = None
        client = None
        run_id = None
        fatal = False
        self._update(job_id, status="running", started_at=_now(), stage="starting", message="Iniciando operación…")
        try:
            db = Database(self.db_path)
            run_id = db.start_run("dashboard_" + config["action"], config)
            self._update(job_id, run_id=run_id)
            if config["action"] == "reclean":
                self._reclean(job_id, db, run_id, config)
            else:
                client = self.client_factory(delay=config["delay"], timeout=config["timeout"])
                if config["action"] == "discover":
                    urls = self._discover(job_id, db, run_id, client, config)
                    self._tick(job_id, total=len(urls), processed=len(urls))
                    self._update(job_id, result={"urls": urls}, message=f"Se encontraron {len(urls)} URL; no se extrajeron artículos.")
                else:
                    self._scrape(job_id, db, run_id, client, config)
        except Exception as exc:
            fatal = True
            self._tick(job_id, failed=1)
            self._error(job_id, self.get_job(job_id)["current_url"], exc)
            self._update(job_id, message=str(exc))
        finally:
            job = self.get_job(job_id)
            progress = job["progress"]
            status = "failed" if fatal or (progress["failed"] and not progress["success"]) else "partial" if progress["failed"] else "completed"
            try:
                if db is not None and run_id is not None:
                    db.finish_run(run_id, {**progress, "errors": job["errors"], "job_id": job_id, "result": job["result"]}, status)
            except Exception as exc:
                status = "failed"
                self._error(job_id, None, exc)
                self._update(job_id, message=f"No se pudo finalizar el registro de la operación: {exc}")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                if db is not None:
                    db.close()
                message = self.get_job(job_id)["message"]
                if not fatal and config["action"] != "discover" and status != "failed":
                    message = f"{progress['success']} procesados, {progress['skipped']} omitidos y {progress['failed']} errores."
                elif status == "failed" and not fatal and progress["failed"]:
                    message = f"La operación terminó con {progress['failed']} errores; revise el detalle."
                with self._lock:
                    self._update(job_id, status=status, stage="finished", finished_at=_now(), current_url=None, message=message)
                    self._active = None
