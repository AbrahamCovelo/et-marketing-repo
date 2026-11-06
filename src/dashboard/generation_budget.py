"""Local estimates and explicit batch quotes. Never calls a provider.

This is a forecast guard, not a provider billing cap: a request already running
can exceed the measured sample. Unknown usage/prices block further paid work.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid

from src.generation.usage import audit_usage, project_adaptation_cost

POLICY_PATH = Path(__file__).resolve().parents[2] / 'config' / 'generation-budget.json'
SELECTION_FIELDS = {'article_ids', 'configuration_ids', 'model', 'reasoning_effort', 'send_draft', 'service_tier'}


def policy():
    data = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('currency') != 'USD'
            or not _money(data.get('project_limit_usd')) or not 0 < data['project_limit_usd'] <= 10000
            or type(data.get('target_adaptations')) is not int or not 1 <= data['target_adaptations'] <= 100000):
        raise ValueError('El presupuesto local no es válido.')
    return data


def _money(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def selection(body):
    if not isinstance(body, dict) or not SELECTION_FIELDS <= set(body):
        raise ValueError('Falta la selección completa para calcular el coste.')
    result = {k: body[k] for k in SELECTION_FIELDS}
    for key in ('article_ids', 'configuration_ids'):
        values = result[key]
        if not isinstance(values, list) or not values or any(type(x) is not int or x <= 0 for x in values):
            raise ValueError('La selección debe contener identificadores positivos.')
        result[key] = sorted(set(values))
    if not isinstance(result['model'], str) or not result['model'].strip() or result['service_tier'] not in ('default', 'flex'):
        raise ValueError('Seleccione el modelo y el nivel de servicio.')
    if type(result['send_draft']) is not bool:
        raise ValueError('La opción de borrador no es válida.')
    effort = result['reasoning_effort']
    if effort is not None and (not isinstance(effort, str) or len(effort) > 32
                              or (effort and not re.fullmatch(r'[a-z0-9_-]+', effort))):
        raise ValueError('El esfuerzo de razonamiento no es válido.')
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def estimate(db_path, body):
    chosen = selection(body)
    count = len(chosen['article_ids']) * len(chosen['configuration_ids'])
    if count > 500:
        raise ValueError('Seleccione como máximo 500 combinaciones.')
    budget = policy()
    audit = audit_usage(db_path)
    projected = project_adaptation_cost(db_path, model=chosen['model'], service_tier=chosen['service_tier'], adaptations=count)
    target = project_adaptation_cost(db_path, model=chosen['model'], service_tier=chosen['service_tier'], adaptations=budget['target_adaptations'])
    spent = audit['totals']['estimated_cost_usd_max']
    cost = projected['estimated_cost_usd_max']
    blockers = []
    if audit['totals']['unpriced_calls'] or audit['totals']['missing_usage_calls']:
        blockers.append('Hay llamadas sin consumo o precio conocido. Revise su facturación antes de continuar.')
    if not _money(spent) or not _money(cost):
        blockers.append('Faltan tarifas verificadas o una muestra completa de evaluación y redacción para estimar este modelo.')
    elif spent + cost > budget['project_limit_usd']:
        blockers.append('El lote superaría el presupuesto del proyecto según el consumo observado.')
    return {'selection': chosen, 'budget': budget, 'spent_usd_max': spent,
            'remaining_usd': max(0, budget['project_limit_usd'] - spent) if _money(spent) else None,
            'projection': projected, 'target_projection': target, 'blockers': blockers,
            'allowed': not blockers, 'max_paid_requests': count * 2,
            'notice': 'Estimación basada en la última adaptación completa. Incluye comprobación, búsquedas y redacción. '
                      'Puede variar con la longitud y el razonamiento; no es un límite de facturación de OpenAI.'}


def create_quote(db_path, body):
    result = estimate(db_path, body)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS generation_cost_quotes (
            id TEXT PRIMARY KEY, selection_hash TEXT NOT NULL, policy_hash TEXT NOT NULL,
            quote_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        key = uuid.uuid4().hex
        db.execute('INSERT INTO generation_cost_quotes VALUES (?,?,?,?,?)',
                   (key, _digest(result['selection']), _digest(result['budget']), json.dumps(result), datetime.now(timezone.utc).isoformat()))
    return {**result, 'quote_id': key}


def validate_quote(db_path, body):
    chosen = selection(body)
    key = body.get('budget_quote_id')
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('Revise y acepte la estimación de esta selección antes de generar.')
    with closing(sqlite3.connect(db_path)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='generation_cost_quotes'").fetchone()
        row = db.execute('SELECT selection_hash,policy_hash,quote_json,created_at FROM generation_cost_quotes WHERE id=?', (key,)).fetchone() if exists else None
    if not row or row[0] != _digest(chosen) or row[1] != _digest(policy()):
        raise ValueError('Revise y acepte la estimación de esta selección antes de generar.')
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[3])).total_seconds()
    except (ValueError, TypeError):
        age = -1
    if not 0 <= age <= 1800:
        raise ValueError('La estimación caducó. Actualícela antes de generar.')
    quoted = json.loads(row[2])
    current = estimate(db_path, body)
    if not quoted['allowed'] or not current['allowed']:
        raise ValueError(' '.join(current['blockers'] or quoted['blockers']))
    if current['projection']['estimated_cost_usd_max'] > quoted['projection']['estimated_cost_usd_max']:
        raise ValueError('Cambió la estimación. Revísela antes de iniciar el lote.')
    return quoted


def check_next_item(db_path, options, article_id, configuration_id):
    if not isinstance(options.get('budget_quote_id'), str) or not re.fullmatch(r'[a-f0-9]{32}', options['budget_quote_id']):
        raise ValueError('El lote no tiene una estimación de coste aceptada; no se iniciarán llamadas nuevas.')
    result = estimate(db_path, {**options, 'article_ids': [article_id], 'configuration_ids': [configuration_id]})
    if not result['allowed']:
        raise ValueError(' '.join(result['blockers']))

