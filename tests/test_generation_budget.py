"""Offline budget forecasts and HTTP/worker spend boundaries."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from src.dashboard import generation_budget as budget
from src.dashboard.app import create_app
from test_generation_jobs import setup, body, budget_prices


def selection(**overrides):
    return body(service_tier='default', **overrides)


def accepted(db_path, **overrides):
    chosen = selection(**overrides)
    return {**chosen, 'budget_quote_id': budget.create_quote(db_path, chosen)['quote_id']}


def test_forecast_is_local_and_counts_both_paid_stages(setup, budget_prices):
    result = budget.create_quote(setup['path'], selection(article_ids=[2, 1, 1], configuration_ids=[3, 1]))
    assert result['allowed'] and result['selection']['article_ids'] == [1, 2]
    assert result['max_paid_requests'] == 8 and result['projection']['adaptations'] == 4
    assert result['projection']['estimated_cost_usd_max'] == 0.08
    assert result['target_projection']['estimated_cost_usd_max'] == 6
    assert result['spent_usd_max'] == 0.25 and result['remaining_usd'] == 14.75
    assert result['budget']['target_adaptations'] == 300
    assert not setup['calls'] and not setup['validations']


@pytest.mark.parametrize('change,value', [
    ('spent', 15), ('spent', None), ('spent', float('nan')),
    ('per_adaptation', None), ('per_adaptation', float('inf')), ('per_adaptation', -1),
    ('unpriced_calls', 1), ('missing_usage_calls', 1),
])
def test_unknown_or_over_budget_consumption_cannot_authorize_work(setup, budget_prices, change, value):
    budget_prices[change] = value
    quoted = accepted(setup['path'])
    with pytest.raises(ValueError):
        budget.validate_quote(setup['path'], quoted)
    assert not setup['calls']


@pytest.mark.parametrize('overrides', [
    {'article_ids':[2]}, {'configuration_ids':[1]}, {'model':'different-model'},
    {'reasoning_effort':'low'}, {'send_draft':True}, {'service_tier':'flex'},
])
def test_quote_is_bound_to_every_cost_affecting_selection_field(setup, budget_prices, overrides):
    chosen = accepted(setup['path'])
    with pytest.raises(ValueError, match='estimación'):
        budget.validate_quote(setup['path'], {**chosen, **overrides})


@pytest.mark.parametrize('key', [None, '', [], {'id':'forged'}, 'forged', 'a' * 32])
def test_forged_or_missing_quote_fails_before_lookup_or_work(setup, budget_prices, key):
    with pytest.raises(ValueError):
        budget.validate_quote(setup['path'], {**selection(), 'budget_quote_id':key})
    assert not setup['validations'] and not setup['calls']


@pytest.mark.parametrize('age', [1801, -100])
def test_expired_or_future_quote_cannot_start(setup, budget_prices, age):
    chosen = accepted(setup['path'])
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    with sqlite3.connect(setup['path']) as db:
        db.execute('UPDATE generation_cost_quotes SET created_at=?', (timestamp,))
    with pytest.raises(ValueError, match='caducó'):
        budget.validate_quote(setup['path'], chosen)


def test_policy_or_observed_estimate_changes_require_a_new_quote(setup, budget_prices):
    chosen = accepted(setup['path'])
    budget_prices['per_adaptation'] = 0.03
    with pytest.raises(ValueError, match='Cambió'):
        budget.validate_quote(setup['path'], chosen)
    budget_prices['per_adaptation'] = 0.02
    policy = json.loads(budget_prices['policy_path'].read_text(encoding='utf-8'))
    policy['project_limit_usd'] = 10
    budget_prices['policy_path'].write_text(json.dumps(policy), encoding='utf-8')
    with pytest.raises(ValueError, match='estimación'):
        budget.validate_quote(setup['path'], chosen)


@pytest.mark.parametrize('limit', [True, float('nan'), float('inf'), -1, 0, '15'])
def test_invalid_policy_is_never_treated_as_unlimited(setup, budget_prices, limit):
    budget_prices['policy_path'].write_text(json.dumps({'currency':'USD', 'project_limit_usd':limit,
                                                      'target_adaptations':300}), encoding='utf-8')
    with pytest.raises(ValueError, match='presupuesto'):
        budget.estimate(setup['path'], selection())


def test_default_manager_requires_a_valid_quote_before_model_or_worker(setup, budget_prices):
    manager = setup['manager'](budget_validator=budget.validate_quote, budget_guard=budget.check_next_item)
    with pytest.raises(ValueError):
        manager.submit(selection())
    assert manager.list() == [] and not setup['validations'] and not setup['tasks']


def test_accepted_quote_duplicate_is_idempotent_even_after_expiry(setup, budget_prices):
    manager = setup['manager'](budget_validator=budget.validate_quote, budget_guard=budget.check_next_item)
    chosen = accepted(setup['path'])
    first = manager.submit(chosen)
    setup['tasks'][0]()
    with sqlite3.connect(setup['path']) as db:
        db.execute("UPDATE generation_cost_quotes SET created_at='2000-01-01T00:00:00+00:00'")
    again = manager.submit(chosen)
    assert again['id'] == first['id'] and again['status'] == 'completed'
    assert len(setup['calls']) == len(setup['validations']) == len(setup['tasks']) == 1


def test_budget_rechecked_before_first_workflow_and_stops_every_item(setup, budget_prices):
    manager = setup['manager'](budget_validator=budget.validate_quote, budget_guard=budget.check_next_item)
    run = manager.submit(accepted(setup['path'], article_ids=[1, 2]))
    budget_prices['spent'] = 15
    setup['tasks'][0]()
    done = manager.get(run['id'])
    assert done['status'] == 'partial' and all(item['status'] == 'stopped' for item in done['items'])
    assert not setup['calls']


@pytest.mark.parametrize('change,value', [('spent', 14.99), ('unpriced_calls', 1), ('missing_usage_calls', 1)])
def test_measured_spend_between_items_stops_next_paid_workflow(setup, budget_prices, change, value):
    def workflow(*args, **kwargs):
        result = setup['workflow'](*args, **kwargs)
        budget_prices[change] = value
        return result
    manager = setup['manager'](workflow=workflow, budget_validator=budget.validate_quote,
                               budget_guard=budget.check_next_item)
    run = manager.submit(accepted(setup['path'], article_ids=[1, 2]))
    setup['tasks'][0]()
    done = manager.get(run['id'])
    assert done['status'] == 'partial' and done['items'][0]['status'] == 'ready'
    assert done['items'][1]['status'] == 'stopped' and len(setup['calls']) == 1


def test_http_cannot_bypass_estimate_or_forge_acceptance(setup, budget_prices):
    app = create_app(setup['path'], test_config={'TESTING':True, 'GENERATION_ENV_PATH':str(setup['env']),
                                               'DEPLOYMENT_ENV_PATH':str(setup['env'])})
    manager = app.extensions['generation_jobs']
    manager.runner, manager.workflow, manager.model_validator = setup['tasks'].append, setup['workflow'], setup['validator']
    client = app.test_client()
    headers = {'X-Panel-Token':app.config['PANEL_TOKEN']}
    url = '/api/generation/runs'
    for payload in (selection(), {**selection(), 'budget_quote_id':'a' * 32},
                    {**selection(), 'budget_quote_id':['a' * 32]}):
        assert client.post(url, json=payload, headers=headers).status_code == 400
    assert not manager.list() and not setup['validations'] and not setup['tasks']
    preview = client.post('/api/generation/cost-preview', json=selection(), headers=headers)
    assert preview.status_code == 200 and preview.json['allowed']
    assert not setup['calls'] and not setup['validations']
    changed = {**selection(article_ids=[2]), 'budget_quote_id':preview.json['quote_id']}
    assert client.post(url, json=changed, headers=headers).status_code == 400
    assert not setup['tasks']
