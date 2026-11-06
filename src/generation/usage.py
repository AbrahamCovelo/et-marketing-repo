"""Read-only local usage audit. No provider calls, schema initialization or writes.

Prices are a dated, replaceable data file, never inferred from model names.
Actual invoice amounts require the provider's billing records. Incomplete search
actions and missing actual service tiers are explicitly marked as uncertain.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3


DEFAULT_SNAPSHOT = Path(__file__).resolve().parents[2] / "config" / "openai-pricing-2026-11-06.json"
_MILLION = Decimal(1_000_000)
_BOGOTA = timezone(timedelta(hours=-5))


def _object(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def usage_fields(usage):
    """The user's six fields, preserving null when a metric was not reported."""
    usage = _object(usage)
    incoming = _object(usage.get("input_tokens_details"))
    outgoing = _object(usage.get("output_tokens_details"))
    return {
        "entrada": usage.get("input_tokens"),
        "entradaCache": incoming.get("cached_tokens"),
        "escrituraCache": incoming.get("cache_write_tokens"),
        "salidaTotal": usage.get("output_tokens"),
        "razonamiento": outgoing.get("reasoning_tokens"),
        "tokensTotales": usage.get("total_tokens"),
    }


def _public_usage(value):
    """Expose documented numeric counters only, never arbitrary provider data."""
    data = _object(value)
    number = lambda x: type(x) is int and x >= 0
    result = {key: data[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')
              if number(data.get(key))}
    for field, names in (('input_tokens_details', ('cached_tokens', 'cache_write_tokens')),
                         ('output_tokens_details', ('reasoning_tokens',))):
        source = _object(data.get(field))
        details = {key: source[key] for key in names if number(source.get(key))}
        if field == 'input_tokens_details':
            ttl = _object(source.get('cache_write_tokens_details'))
            ttl = {key: ttl[key] for key in ('ttl_30m_tokens', 'ttl_12h_tokens') if number(ttl.get(key))}
            if ttl:
                details['cache_write_tokens_details'] = ttl
        if details:
            result[field] = details
    return result


def load_pricing_snapshot(path=None):
    snapshot = json.loads(Path(path or DEFAULT_SNAPSHOT).read_text(encoding="utf-8"))
    if snapshot.get("version") != 1 or snapshot.get("currency") != "USD":
        raise ValueError("La referencia de precios no tiene un formato compatible.")
    return snapshot


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Contador de tokens ausente o inválido.")
    return value


def web_search_usage(response):
    """Search is chargeable; opening/finding within pages is not another search.

An unfinished search is an uncertainty, not proof of either free or billed use.
Unknown action types also stay uncertain rather than silently disappearing.
"""
    result = {"completed_searches": 0, "unfinished_searches": 0,
              "page_operations": 0, "unknown_actions": 0}
    for item in _object(response).get("output", []):
        if not isinstance(item, dict) or item.get("type") != "web_search_call":
            continue
        action = _object(item.get("action")).get("type")
        if action == "search":
            key = "completed_searches" if item.get("status") == "completed" else "unfinished_searches"
        elif action in {"open_page", "find_in_page"}:
            key = "page_operations"
        else:
            key = "unknown_actions"
        result[key] += 1
    return result


def estimate_usage(usage, *, model, service_tier, snapshot=None, request=None,
                   search=None, created_at=None):
    """Estimate one text Responses request; return unknown rather than invent rates.

Cached reads and writes are disjoint subsets of input. Reasoning is already
included in output. Search-content tokens are already included in usage input.
The cost interval only covers uncertain search actions, not invoice differences.
"""
    snapshot = snapshot if snapshot is not None else load_pricing_snapshot()
    usage, request = _object(usage), _object(request)
    search = search or {}
    result = {"currency": "USD", "estimated_cost_usd_min": None,
              "estimated_cost_usd_max": None, "token_cost_usd": None,
              "tool_cost_usd_min": None, "tool_cost_usd_max": None,
              "warnings": [], "pricing_checked_at": snapshot.get("checked_at")}
    model_price = next((item for item in snapshot.get("models", []) if model in item.get("ids", [])), None)
    rates = (model_price or {}).get("tiers", {}).get(service_tier)
    if not rates:
        result["warnings"].append("No hay una tarifa verificada para este modelo y nivel de servicio.")
        return result
    expired = model_price.get("valid_until")
    if expired and created_at and str(created_at)[:10] > expired:
        result["warnings"].append("La tarifa documentada ya había vencido en la fecha de esta llamada.")
        return result
    try:
        fields = usage_fields(usage)
        incoming, outgoing = _count(fields["entrada"]), _count(fields["salidaTotal"])
        cached = _count(fields["entradaCache"]) if fields["entradaCache"] is not None else 0
        writes = _count(fields["escrituraCache"]) if fields["escrituraCache"] is not None else 0
        if incoming > model_price.get("maximum_input_tokens", incoming):
            raise ValueError("La entrada excede el rango de contexto cubierto por esta referencia de precios.")
        ordinary = incoming - cached - writes
        if ordinary < 0:
            raise ValueError("La suma de lecturas y escrituras de caché supera la entrada total.")
        details = _object(_object(usage.get("input_tokens_details")).get("cache_write_tokens_details"))
        if details:
            short = _count(details.get("ttl_30m_tokens"))
            long = _count(details.get("ttl_12h_tokens"))
            if short + long != writes:
                raise ValueError("Los componentes de escritura de caché no coinciden con su total.")
        else:
            ttl = _object(request.get("prompt_cache_options")).get("ttl", "30m")
            if ttl not in {"30m", "12h"}:
                raise ValueError("No se reconoce la duración de la caché.")
            short, long = (writes, 0) if ttl == "30m" else (0, writes)
            if writes:
                result["warnings"].append("Sin desglose de escrituras por duración; se usa la duración solicitada o 30m por defecto.")
        amounts = {"input": ordinary, "cached_input": cached,
                   "cache_write_30m": short, "cache_write_12h": long, "output": outgoing}
        components = {key: Decimal(str(rates[key])) * tokens / _MILLION
                      for key, tokens in amounts.items() if tokens}
        if any(value < 0 for value in components.values()):
            raise ValueError("Tarifa inválida.")
        token_cost = sum(components.values(), Decimal(0))
        search_rate = Decimal(str(snapshot["web_search_usd_per_call"]))
        completed = _count(search.get("completed_searches", 0))
        uncertain = _count(search.get("unfinished_searches", 0)) + _count(search.get("unknown_actions", 0))
        tool_min, tool_max = search_rate * completed, search_rate * (completed + uncertain)
        if uncertain:
            result["warnings"].append("Hay búsquedas sin finalizar o sin acción identificable; su facturación no se puede confirmar con la respuesta local.")
        if fields["entradaCache"] is None or fields["escrituraCache"] is None:
            result["warnings"].append("La respuesta no informa todos los contadores de caché; los ausentes se han tratado como cero.")
        result.update(token_cost_usd=float(token_cost),
                      tool_cost_usd_min=float(tool_min), tool_cost_usd_max=float(tool_max),
                      estimated_cost_usd_min=float(token_cost + tool_min),
                      estimated_cost_usd_max=float(token_cost + tool_max),
                      input_breakdown={"ordinary": ordinary, "cached": cached,
                                       "cache_write_30m": short, "cache_write_12h": long},
                      token_cost_components_usd={key: float(value) for key, value in components.items()})
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        result["warnings"].append(str(exc) if isinstance(exc, ValueError) else "Faltan contadores o tarifas para estimar el coste.")
    return result


def _local_time(value):
    try:
        return datetime.fromisoformat(value).astimezone(_BOGOTA).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _record(row, stage, deleted, snapshot):
    request, response = _object(row.get("request_json")), _object(row.get("response_json"))
    usage = _public_usage(_object(row.get("usage_json")) or _object(response.get("usage")))
    actual_tier = response.get("service_tier")
    tier = actual_tier or request.get("service_tier") or row.get("service_tier")
    model = row.get("response_model") or response.get("model") or row.get("model")
    search = web_search_usage(response)
    cost = estimate_usage(usage, model=model, service_tier=tier, snapshot=snapshot,
                          request=request, search=search, created_at=row.get("created_at"))
    if not actual_tier:
        cost["warnings"].append("La respuesta no conserva el nivel de servicio aplicado; se usa el solicitado como supuesto.")
    return {"id": row["id"], "stage": stage, "run_key": row.get("run_key"),
            "article_id": row.get("article_id"), "configuration_id": row.get("configuration_id"),
            "status": row.get("status"), "model": model, "service_tier": tier,
            "service_tier_confirmed": bool(actual_tier), "deleted_locally": row["id"] in deleted if stage == "generation" else False,
            "created_at": row.get("created_at"), "created_at_bogota": _local_time(row.get("created_at")),
            "reasoning_effort": _object(request.get("reasoning")).get("effort"),
            "usage": usage, "usage_fields": usage_fields(usage), "web_search": search, "cost": cost}


def _totals(records):
    summed = {key: sum(record["usage_fields"].get(key) or 0 for record in records)
              for key in usage_fields({})}
    unknown = sum(record["cost"]["estimated_cost_usd_min"] is None for record in records)
    lower = sum(Decimal(str(record["cost"]["estimated_cost_usd_min"])) for record in records
                if record["cost"]["estimated_cost_usd_min"] is not None)
    upper = sum(Decimal(str(record["cost"]["estimated_cost_usd_max"])) for record in records
                if record["cost"]["estimated_cost_usd_max"] is not None)
    return {"calls": len(records), "usage_fields": summed,
            "estimated_cost_usd_min": float(lower) if not unknown else None,
            "estimated_cost_usd_max": float(upper) if not unknown else None,
            "priced_subtotal_usd_min": float(lower), "priced_subtotal_usd_max": float(upper),
            "unpriced_calls": unknown,
            "unconfirmed_service_tier_calls": sum(not r["service_tier_confirmed"] for r in records),
            "missing_usage_calls": sum(not r["usage"] for r in records),
            "stages": {stage: sum(r["stage"] == stage for r in records) for stage in ("assessment", "generation")}}


def audit_usage(db_path, *, snapshot_path=None, article_id=None, trial_id=None):
    """Read all recorded attempts, including deleted content; never open writable DB.

Filtering a trial includes its assessment only when run keys explicitly pair
them. Arbitrarily joining another assessment for the same article is unsafe.
No content, credentials, provider response IDs or request bodies are returned.
"""
    snapshot = load_pricing_snapshot(snapshot_path)
    path = Path(db_path).resolve()
    records = []
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            deleted = {r[0] for r in connection.execute("SELECT trial_id FROM adaptation_deletions")} if "adaptation_deletions" in tables else set()
            for table, stage in (("generation_assessments", "assessment"), ("generation_trials", "generation")):
                if table not in tables:
                    continue
                for row in connection.execute("SELECT * FROM " + table):
                    if article_id is None or row["article_id"] == article_id:
                        records.append(_record(dict(row), stage, deleted, snapshot))
    records.sort(key=lambda r: (r["created_at"] or "", r["stage"], r["id"]))
    if trial_id is not None:
        trial = next((r for r in records if r["stage"] == "generation" and r["id"] == trial_id), None)
        key = trial.get("run_key") or "" if trial else ""
        assessment_key = key[:-len(".generation")] + ".assessment" if key.endswith(".generation") else None
        records = [r for r in records if r is trial or
                   (r["stage"] == "assessment" and assessment_key and r["run_key"] == assessment_key)]
    grouped = []
    for trial in (r for r in records if r['stage'] == 'generation'):
        key = trial.get('run_key') or ''
        expected = key[:-len('.generation')] + '.assessment' if key.endswith('.generation') else None
        pair = [r for r in records if r is trial or (expected and r['stage'] == 'assessment'
                and r['run_key'] == expected and r['article_id'] == trial['article_id']
                and r['configuration_id'] == trial['configuration_id'])]
        totals = _totals(pair)
        missing = bool(expected and len(pair) < 2)
        if missing:
            totals['estimated_cost_usd_min'] = totals['estimated_cost_usd_max'] = None
        grouped.append({'trial_id': trial['id'], 'article_id': trial['article_id'],
                        'model': trial['model'], 'deleted_locally': trial['deleted_locally'],
                        'totals': totals, 'missing_assessment': missing})
    return {"records": records, "totals": _totals(records), 'trials': grouped,
            "pricing": {key: snapshot.get(key) for key in ("checked_at", "currency", "source_urls", "notice")},
            "scope": "Registros locales; no consulta el consumo ni la facturación de la cuenta OpenAI."}


def summarize_trial_usage(db_path, trial_id, *, snapshot_path=None):
    return audit_usage(db_path, snapshot_path=snapshot_path, trial_id=trial_id)


def trial_usage_summaries(connection, trial_ids, *, snapshot_path=None):
    """Combine each generation with its own assessment, using the existing DB read.

    The association requires run key, article and configuration to match. Legacy
    standalone trials have one stage; a missing expected stage is never zero cost.
    """
    if not trial_ids:
        return {}
    snapshot = load_pricing_snapshot(snapshot_path)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'generation_trials' not in tables:
        return {}
    placeholders = ','.join('?' for _ in trial_ids)
    # Do not load the article HTML or editorial snapshots merely to count tokens.
    columns = 'id,run_key,article_id,configuration_id,model,status,request_json,response_json,usage_json,created_at'
    cursor = connection.execute(f'SELECT {columns} FROM generation_trials WHERE id IN ({placeholders})', trial_ids)
    trials = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor]
    expected = {row['run_key'][:-len('.generation')] + '.assessment'
                for row in trials if row['run_key'].endswith('.generation')}
    assessments = {}
    if expected and 'generation_assessments' in tables:
        cursor = connection.execute(f"SELECT {columns} FROM generation_assessments WHERE run_key IN ({','.join('?' for _ in expected)})", sorted(expected))
        for row in cursor:
            data = dict(zip([c[0] for c in cursor.description], row))
            assessments[(data['run_key'], data['article_id'], data['configuration_id'])] = data
    result = {}
    for trial in trials:
        records, missing = [], False
        if trial['run_key'].endswith('.generation'):
            key = (trial['run_key'][:-len('.generation')] + '.assessment', trial['article_id'], trial['configuration_id'])
            assessment = assessments.get(key)
            if assessment:
                records.append(_record(assessment, 'assessment', set(), snapshot))
            else:
                missing = True
        records.append(_record(trial, 'generation', set(), snapshot))
        totals = _totals(records)
        complete = not missing and not totals['missing_usage_calls']
        if missing:
            totals['estimated_cost_usd_min'] = totals['estimated_cost_usd_max'] = None
        result[trial['id']] = {
            'records': records, 'totals': totals, 'complete': complete,
            'missing_assessment': missing, 'currency': 'USD', 'billing_verified': False,
            'notice': ('Falta el consumo de la evaluación asociada; el total está incompleto.' if missing
                       else 'Consumo registrado de todas las etapas de esta prueba. Coste estimado con tarifas públicas; no es una factura.'),
        }
    return result


def project_adaptation_cost(db_path, *, model, service_tier="default", adaptations=300,
                            sample_trial_id=None, snapshot_path=None):
    """Replay measured usage at target rates; a comparison, never a budget ceiling.

The sample must contain both paid stages with recorded usage. Output lengths,
reasoning and research can differ with another model or article.
"""
    if type(adaptations) is not int or not 1 <= adaptations <= 100000:
        raise ValueError("Indique un número de adaptaciones entre 1 y 100000.")
    snapshot = load_pricing_snapshot(snapshot_path)
    audit = audit_usage(db_path, snapshot_path=snapshot_path)
    trials = [r for r in reversed(audit["records"]) if r["stage"] == "generation"
              and (sample_trial_id is None or r["id"] == sample_trial_id)]
    sample = []
    for trial in trials:
        key = trial.get("run_key") or ""
        if not key.endswith(".generation"):
            continue
        assessment_key = key[:-len(".generation")] + ".assessment"
        assessment = next((r for r in audit["records"] if r["stage"] == "assessment"
                           and r["run_key"] == assessment_key), None)
        if assessment and all(r["usage"] and r["status"] == "completed" for r in (assessment, trial)):
            sample = [assessment, trial]
            break
    result = {"model": model, "service_tier": service_tier, "adaptations": adaptations,
              "sample_trial_id": sample[-1]["id"] if sample else None,
              "sample_article_id": sample[-1]["article_id"] if sample else None,
              "stages_per_adaptation": 2, "estimated_cost_usd_min": None,
              "estimated_cost_usd_max": None, "per_adaptation_usd_min": None,
              "per_adaptation_usd_max": None, "pricing_checked_at": snapshot.get("checked_at"),
              "notice": "Comparación con los mismos tokens y búsquedas de una adaptación real. No es un límite de gasto: otros artículos, modelos o esfuerzos pueden consumir más o menos.",
              "warnings": []}
    if not sample:
        result["warnings"].append("No hay una adaptación completa con sus dos etapas y consumo registrado.")
        return result
    estimates = [estimate_usage(r["usage"], model=model, service_tier=service_tier,
                                snapshot=snapshot, search=r["web_search"],
                                created_at=datetime.now(timezone.utc).date().isoformat()) for r in sample]
    result["warnings"] = list(dict.fromkeys(warning for estimate in estimates for warning in estimate["warnings"]))
    if all(estimate["estimated_cost_usd_min"] is not None for estimate in estimates):
        low = sum(Decimal(str(e["estimated_cost_usd_min"])) for e in estimates)
        high = sum(Decimal(str(e["estimated_cost_usd_max"])) for e in estimates)
        result.update(per_adaptation_usd_min=float(low), per_adaptation_usd_max=float(high),
                      estimated_cost_usd_min=float(low * adaptations),
                      estimated_cost_usd_max=float(high * adaptations))
    return result
