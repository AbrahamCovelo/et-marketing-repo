"""Explicit panel execution with fake providers and real durable job storage."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import threading

import pytest
import requests

from src.dashboard.app import create_app
from src.dashboard.generation_jobs import GenerationJobManager
from src.dashboard.jobs import JobBusyError
from src.deployment.repository import DeploymentRepository
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.presets import load_editorial_prompts


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    for suffix in ('USERNAME', 'APPLICATION_PASSWORD'):
        monkeypatch.delenv('WP_COLOMBIANS_ES_' + suffix, raising=False)
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: pytest.fail('Unexpected external HTTP'))
    path, env = tmp_path / 'articles.db', tmp_path / '.env'
    env.write_text('OPENAI_API_KEY=fixture-openai-secret\nWP_COLOMBIANS_ES_USERNAME=fixture-user\n'
                   'WP_COLOMBIANS_ES_APPLICATION_PASSWORD=fixture-wordpress-secret\n', encoding='utf-8')
    with Database(path) as db:
        for index in (1, 2):
            url = f'https://movingtospain.com/guide-{index}/'
            db.store_article(ExtractionResult(url=url, canonical_url=url, title=f'Guide {index}',
                original_text='Base article', blocks=[{'key':'b1', 'position':0, 'source_order':1,
                'kind':'paragraph', 'text':'Base article', 'section_path':[], 'data':{}, 'flags':[]}]),
                raw_html='<p>Base article</p>')
    load_editorial_prompts(path)
    DeploymentRepository(path, env_path=env)
    calls, validations, tasks = [], [], []
    result = {'status': 'ready', 'stage':'export', 'trial_id':12,
              'artifacts':{'body':'PRIVATE/filesystem/location.html'}}
    def workflow(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(result)
    def validator(model, effort, **kwargs):
        validations.append((model, effort, kwargs))
        return {'model':model, 'reasoning_effort': effort or None}
    def manager(**kwargs):
        return GenerationJobManager(path, env_path=env, cache_path=tmp_path / 'models.json',
            runner=kwargs.pop('runner', tasks.append), workflow=kwargs.pop('workflow', workflow),
            model_validator=kwargs.pop('model_validator', validator),
            budget_validator=kwargs.pop('budget_validator', lambda *args: None),
            budget_guard=kwargs.pop('budget_guard', lambda *args: None), **kwargs)
    return {'path':path, 'env':env, 'calls':calls, 'validations':validations,
            'tasks':tasks, 'result':result, 'manager':manager, 'validator':validator, 'workflow':workflow}


def body(**kwargs):
    return {'article_ids':[1], 'configuration_ids':[3], 'model':'gpt-6-luna',
            'reasoning_effort':'high', 'send_draft':False, 'request_key':'fixture-operation-1', **kwargs}


@pytest.fixture
def budget_prices(tmp_path, monkeypatch):
    """Known local price/usage observations; quote creation and guards stay real."""
    from src.dashboard import generation_budget
    policy_path = tmp_path / 'budget.json'
    policy_path.write_text(json.dumps({'currency':'USD', 'project_limit_usd':15,
                                       'target_adaptations':300}), encoding='utf-8')
    monkeypatch.setattr(generation_budget, 'POLICY_PATH', policy_path)
    state = {'spent':0.25, 'per_adaptation':0.02, 'unpriced_calls':0, 'missing_usage_calls':0,
             'projections':[], 'policy_path':policy_path}
    def audit(*args, **kwargs):
        return {'totals': {'estimated_cost_usd_max':state['spent'],
                'unpriced_calls':state['unpriced_calls'], 'missing_usage_calls':state['missing_usage_calls']}}
    def project(db, *, model, service_tier, adaptations):
        state['projections'].append((model, service_tier, adaptations))
        amount = state['per_adaptation']
        return {'estimated_cost_usd_max': amount * adaptations if amount is not None else None,
                'estimated_cost_usd_min': amount * adaptations if amount is not None else None,
                'adaptations':adaptations, 'sample_trial_id':4}
    monkeypatch.setattr(generation_budget, 'audit_usage', audit)
    monkeypatch.setattr(generation_budget, 'project_adaptation_cost', project)
    return state


def test_init_and_reads_do_not_invoke_model_validation_or_workflow(setup):
    manager = setup['manager']()
    assert manager.list() == []
    with pytest.raises(KeyError):
        manager.get(999)
    assert setup['calls'] == setup['validations'] == setup['tasks'] == []


@pytest.mark.parametrize('send_draft', [False, True])
def test_explicit_submit_reserves_before_worker_and_propagates_selection(setup, send_draft):
    manager = setup['manager']()
    setup['result']['status'] = 'draft' if send_draft else 'ready'
    submitted = manager.submit(body(model='gpt-future-text', reasoning_effort='xhigh', send_draft=send_draft))
    assert submitted['status'] == 'queued' and submitted['total'] == 1
    assert submitted['finished_count'] == 0 and setup['calls'] == []
    assert len(setup['tasks']) == len(setup['validations']) == 1
    setup['tasks'][0]()
    done = manager.get(submitted['id'])
    assert done['status'] == 'completed' and done['finished_count'] == 1
    args, kwargs = setup['calls'][0]
    assert args == (str(setup['path']), 1, 3, 3, 'gpt-future-text', f"panel-{done['id']}-0")
    assert kwargs['reasoning_effort'] == 'xhigh' and kwargs['service_tier'] == 'default'
    assert kwargs['send_draft'] is send_draft and kwargs['import_cover'] is send_draft
    assert kwargs['allow_pending_ctas'] is send_draft
    assert 'PRIVATE' not in json.dumps(done)
    assert not {'request_json', 'response_json', 'payload_hash'} & done.keys()


def test_provider_default_effort_is_preserved_for_workflow(setup):
    manager = setup['manager'](runner=lambda task: task())
    assert manager.submit(body(reasoning_effort=None))['status'] == 'completed'
    assert setup['calls'][0][1]['reasoning_effort'] == ''


def test_provider_default_omits_reasoning_from_both_real_request_builders(setup):
    from test_assessment import Session as AssessmentSession
    from test_generation_trial import Session as TrialSession, result_body
    from src.generation.assessment import run_assessment
    from src.generation.trial import run_trial

    assessment_session = AssessmentSession()
    assessed = run_assessment(setup['path'], 1, 3, 'gpt-6-luna', 'default-effort.assessment',
        reasoning_effort='', env_path=setup['env'], session=assessment_session)
    assert assessed['status'] == 'completed'
    trial_session = TrialSession(body=result_body(model='gpt-6-luna'))
    generated = run_trial(setup['path'], 1, 3, 'gpt-6-luna', 'default-effort.generation',
        reasoning_effort='', env_path=setup['env'], session=trial_session)
    assert generated['status'] == 'completed'
    assert 'reasoning' not in assessment_session.calls[0][2]['json']
    assert 'reasoning' not in trial_session.calls[0][2]['json']


def test_batch_uses_each_article_configuration_pair_once_and_distinct_run_keys(setup):
    manager = setup['manager'](runner=lambda task: task())
    done = manager.submit(body(article_ids=[2,1,1], configuration_ids=[3,1,3]))
    assert done['status'] == 'completed' and done['total'] == done['finished_count'] == 4
    assert {(args[1],args[2]) for args, _ in setup['calls']} == {(1,1),(1,3),(2,1),(2,3)}
    assert len({args[-1] for args, _ in setup['calls']}) == 4
    assert done['options']['article_ids'] == [1,2] and done['options']['configuration_ids'] == [1,3]


@pytest.mark.parametrize('payload', [
    None, [], {}, body(article_ids=[]), body(configuration_ids=[]), body(article_ids=[True]),
    body(configuration_ids=['3']), body(send_draft=1), body(request_key='short'), body(extra=True),
    body(article_ids=list(range(1,252)), configuration_ids=[1,3]),
])
def test_malformed_requests_never_reserve_or_start(setup, payload):
    manager = setup['manager']()
    with pytest.raises(ValueError):
        manager.submit(payload)
    assert manager.list() == [] and not setup['calls'] and not setup['tasks']


@pytest.mark.parametrize('overrides', [{'article_ids':[999]}, {'configuration_ids':[999]}])
def test_unknown_article_or_configuration_cannot_start(setup, overrides):
    manager = setup['manager']()
    with pytest.raises((ValueError, KeyError)):
        manager.submit(body(**overrides))
    assert manager.list() == [] and not setup['calls'] and not setup['tasks']


def test_invalid_model_and_effort_are_rejected_before_reservation(setup):
    def invalid(*args, **kwargs):
        raise ValueError('Esfuerzo no admitido')
    manager = setup['manager'](model_validator=invalid)
    with pytest.raises(ValueError, match='Esfuerzo'):
        manager.submit(body())
    assert not setup['tasks'] and manager.list() == []


def test_missing_api_key_prevents_even_model_lookup(setup):
    setup['env'].write_text('', encoding='utf-8')
    manager = setup['manager']()
    with pytest.raises(ValueError, match='clave local'):
        manager.submit(body())
    assert setup['validations'] == [] and manager.list() == []


def test_local_adaptation_needs_no_wordpress_credentials_but_draft_does(setup):
    setup['env'].write_text('OPENAI_API_KEY=fixture-key\n', encoding='utf-8')
    manager = setup['manager']()
    with pytest.raises(ValueError, match='credenciales WordPress'):
        manager.submit(body(send_draft=True))
    assert manager.list() == []
    assert manager.submit(body())['status'] == 'queued'


def test_french_draft_is_rejected_before_spending_but_local_generation_allowed(setup):
    manager = setup['manager']()
    with pytest.raises(ValueError, match='idioma'):
        manager.submit(body(configuration_ids=[2], send_draft=True))
    assert not setup['calls'] and manager.list() == []
    assert manager.submit(body(configuration_ids=[2]))['status'] == 'queued'


def test_duplicate_request_reuses_queued_and_completed_run_without_new_lookup(setup):
    manager = setup['manager']()
    first = manager.submit(body())
    assert manager.submit(body()) == first
    assert len(setup['tasks']) == len(setup['validations']) == 1
    setup['tasks'][0]()
    assert manager.submit(body())['status'] == 'completed'
    assert len(setup['calls']) == len(setup['tasks']) == len(setup['validations']) == 1


def test_reusing_key_for_changed_selection_is_rejected(setup):
    manager = setup['manager']()
    manager.submit(body())
    with pytest.raises(ValueError, match='otra selección'):
        manager.submit(body(article_ids=[2]))
    assert len(setup['tasks']) == 1


def test_other_new_request_cannot_overlap_active_run(setup):
    manager = setup['manager']()
    manager.submit(body())
    with pytest.raises(JobBusyError):
        manager.submit(body(request_key='fixture-operation-2'))
    assert len(manager.list()) == len(setup['tasks']) == 1


def test_concurrent_duplicate_requests_reserve_and_launch_only_once(setup):
    barrier = threading.Barrier(2)
    def validate(*args, **kwargs):
        barrier.wait(timeout=10)
        return setup['validator'](*args, **kwargs)
    manager = setup['manager'](model_validator=validate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(manager.submit, body()) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0]['id'] == results[1]['id']
    assert len(manager.list()) == len(setup['tasks']) == 1
    setup['tasks'][0]()
    assert len(setup['calls']) == 1


def test_worker_claim_is_not_replayed_if_runner_calls_task_twice(setup):
    manager = setup['manager']()
    manager.submit(body())
    task = setup['tasks'][0]
    task()
    task()
    assert len(setup['calls']) == 1


def test_concurrent_workers_cannot_execute_same_reserved_job_twice(setup):
    entered, release = threading.Event(), threading.Event()
    def workflow(*args, **kwargs):
        setup['calls'].append((args, kwargs))
        entered.set()
        assert release.wait(timeout=10)
        return deepcopy(setup['result'])
    manager = setup['manager'](workflow=workflow)
    first = manager.submit(body())
    task = setup['tasks'][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_worker = pool.submit(task)
        assert entered.wait(timeout=10)
        second_worker = pool.submit(task)
        try:
            second_worker.result(timeout=5)
        finally:
            release.set()
        first_worker.result(timeout=10)
    assert len(setup['calls']) == 1 and manager.get(first['id'])['status'] == 'completed'


def test_opening_manager_again_never_resumes_paid_work_automatically(setup):
    manager = setup['manager']()
    first = manager.submit(body())
    reopened = setup['manager']()
    assert reopened.get(first['id'])['status'] == 'queued'
    assert len(setup['tasks']) == 1 and setup['calls'] == []


@pytest.mark.parametrize('result', [
    {'status':'stopped', 'assessment_status':'unknown'},
    {'status':'stopped', 'generation_status':'running'},
    {'status':'stopped', 'cover_status':'importing'},
    {'status':'unknown', 'stage':'deployment'},
    {'status':'sending', 'stage':'deployment'},
])
def test_uncertain_stage_stops_remaining_items_and_stays_unknown(setup, result):
    setup['result'].clear()
    setup['result'].update(result)
    manager = setup['manager'](runner=lambda task: task())
    payload = body(article_ids=[1,2])
    done = manager.submit(payload)
    assert done['status'] == 'unknown'
    assert done['items'][1]['status'] == 'stopped'
    assert len(setup['calls']) == 1
    assert manager.submit(payload)['id'] == done['id'] and len(setup['calls']) == 1


def test_exception_is_sanitized_and_remaining_articles_not_called(setup):
    def broken(*args, **kwargs):
        setup['calls'].append((args,kwargs))
        raise requests.ConnectionError('fixture-openai-secret PRIVATE/exception')
    manager = setup['manager'](runner=lambda task: task(), workflow=broken)
    payload = body(article_ids=[1,2])
    done = manager.submit(payload)
    assert done['status'] == 'unknown' and done['items'][1]['status'] == 'stopped'
    assert len(setup['calls']) == 1
    assert 'PRIVATE' not in json.dumps(done) and 'fixture-openai-secret' not in json.dumps(done)
    assert manager.submit(payload)['id'] == done['id'] and len(setup['calls']) == 1


def test_failed_launch_is_durable_and_same_request_does_not_retry(setup):
    def broken_runner(task):
        raise RuntimeError('PRIVATE runner exception')
    manager = setup['manager'](runner=broken_runner)
    with pytest.raises(JobBusyError, match='registrada'):
        manager.submit(body())
    assert manager.list()[0]['status'] == 'unknown'
    assert manager.submit(body())['status'] == 'unknown' and setup['calls'] == []


def test_changed_context_stops_item_before_workflow(setup):
    manager = setup['manager']()
    first = manager.submit(body())
    with sqlite3.connect(setup['path']) as db:
        db.execute("UPDATE generation_destinations SET notes='Updated audience description' WHERE id=3")
    setup['tasks'][0]()
    done = manager.get(first['id'])
    assert done['status'] == 'partial' and done['items'][0]['status'] == 'stopped'
    assert not setup['calls']


def test_applicability_omission_counts_as_completed_without_draft(setup):
    setup['result'].update(status='stopped', decision='omit', stage='assessment')
    manager = setup['manager'](runner=lambda task: task())
    done = manager.submit(body())
    assert done['status'] == 'completed' and done['items'][0]['result']['decision'] == 'omit'


def test_execution_api_csrf_origin_reads_and_explicit_start(setup, budget_prices):
    app = create_app(setup['path'], test_config={'TESTING':True, 'GENERATION_ENV_PATH':str(setup['env']),
                                               'DEPLOYMENT_ENV_PATH':str(setup['env'])})
    manager = app.extensions['generation_jobs']
    manager.runner = setup['tasks'].append
    manager.workflow, manager.model_validator = setup['workflow'], setup['validator']
    client = app.test_client()
    url = '/api/generation/runs'
    headers = {'X-Panel-Token':app.config['PANEL_TOKEN']}
    assert client.get(url).json == {'items':[]}
    assert client.get(url + '/999').status_code == 404
    assert setup['validations'] == setup['calls'] == []
    assert client.post(url, json=body()).status_code == 403
    assert client.post(url, json=body(), headers={**headers,'Origin':'https://evil.example'}).status_code == 403
    assert client.post(url, json={}, headers=headers).status_code == 400
    selection = body(service_tier='default')
    preview = client.post('/api/generation/cost-preview', json=selection, headers=headers)
    assert preview.status_code == 200 and preview.json['allowed']
    quoted_body = {**selection, 'budget_quote_id':preview.json['quote_id']}
    response = client.post(url, json=quoted_body, headers=headers)
    assert response.status_code == 202 and response.json['status'] == 'queued'
    run_id = response.json['id']
    assert client.get(f'{url}/{run_id}').json['status'] == 'queued'
    assert len(setup['validations']) == 1 and setup['calls'] == []
    setup['tasks'][0]()
    assert client.get(f'{url}/{run_id}').json['status'] == 'completed'
    assert client.post(url, json=quoted_body, headers=headers).json['id'] == run_id
    assert len(setup['calls']) == 1
