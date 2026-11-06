"""A format-blocked finished output resumes without another AI generation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from src.dashboard.app import create_app
from src.dashboard import generation_jobs
from src.dashboard.jobs import JobBusyError
from test_generation_jobs import setup, body


def blocked_run(setup, monkeypatch, *, send_draft=False, article_ids=None):
    setup['result'].clear()
    setup['result'].update(status='stopped', stage='generation', trial_id=12, assessment_id=7,
        generation_status='completed', assessment_status='completed', decision='adapt',
        semantic_validation={'valid':False, 'errors':['Bare CTA outside a paragraph']},
        reason='Invalid format')
    manager = setup['manager']()
    run = manager.submit(body(send_draft=send_draft, article_ids=article_ids or [1]))
    setup['tasks'].pop()()
    run = manager.get(run['id'])
    trial = {'id':12, 'article_id':1, 'configuration_id':3, 'model':run['options']['model'],
             'status':'completed', 'run_key':f"panel-{run['id']}-0.generation"}
    assessment = {**trial, 'id':7, 'run_key':f"panel-{run['id']}-0.assessment"}
    reads = []
    def get_trial(db_path, trial_id):
        reads.append(('trial', trial_id))
        return deepcopy(trial)
    def get_assessment(db_path, assessment_id):
        reads.append(('assessment', assessment_id))
        return deepcopy(assessment)
    monkeypatch.setattr(generation_jobs, 'get_trial', get_trial)
    monkeypatch.setattr(generation_jobs, 'get_assessment', get_assessment)
    setup['calls'].clear()
    setup['validations'].clear()
    return manager, run, trial, assessment, reads


@pytest.mark.parametrize('send_draft', [False, True])
def test_recovery_uses_saved_outputs_and_original_options_only(setup, monkeypatch, send_draft):
    manager, run, _, _, reads = blocked_run(setup, monkeypatch, send_draft=send_draft)
    assert run['status'] == 'partial' and run['can_resume_saved']
    resumed = manager.resume_saved(run['id'])
    assert resumed['id'] == run['id'] and resumed['status'] == 'queued'
    assert resumed['options'] == run['options']
    assert reads == [('trial',12), ('assessment',7)]
    assert setup['validations'] == setup['calls'] == []
    assert len(setup['tasks']) == 1
    assert resumed['items'][0]['recovery_history'][0]['result'] == run['items'][0]['result']
    setup['result'].clear()
    setup['result'].update(status='draft' if send_draft else 'ready', stage='deployment' if send_draft else 'export', trial_id=12)
    setup['tasks'].pop()()
    done = manager.get(run['id'])
    assert done['status'] == 'completed' and not done['can_resume_saved']
    args, kwargs = setup['calls'][0]
    assert args[4:] == (run['options']['model'], f"panel-{run['id']}-0")
    assert kwargs['reuse_only'] is True
    assert kwargs['reasoning_effort'] == 'high'
    assert kwargs['send_draft'] is kwargs['import_cover'] is send_draft
    assert len(manager.list()) == 1


def test_concurrent_resume_reserves_only_one_worker(setup, monkeypatch):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: manager.resume_saved(run['id']), range(2)))
    assert all(result['id'] == run['id'] for result in results)
    assert len(setup['tasks']) == 1 and setup['calls'] == []
    assert len(manager.get(run['id'])['items'][0]['recovery_history']) == 1


def test_recovery_does_not_rerun_other_completed_items(setup, monkeypatch):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch, article_ids=[1,2])
    items = run['items']
    items[1].update(status='ready', reason='', result={'status':'ready','stage':'export','trial_id':13})
    manager._save(run['id'], 'partial', items)
    previous_second = deepcopy(items[1])
    manager.resume_saved(run['id'])
    setup['result'].clear()
    setup['result'].update(status='ready', stage='export', trial_id=12)
    setup['tasks'].pop()()
    done = manager.get(run['id'])
    assert len(setup['calls']) == 1 and setup['calls'][0][0][1] == 1
    assert done['items'][1] == previous_second


@pytest.mark.parametrize('failure', ['unknown', 'exception'])
def test_failed_recovery_preserves_completed_siblings(setup, monkeypatch, failure):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch, article_ids=[1,2])
    items = run['items']
    items[1].update(status='ready', reason='', result={'status':'ready','stage':'export','trial_id':13})
    manager._save(run['id'], 'partial', items)
    previous_second = deepcopy(items[1])
    manager.resume_saved(run['id'])
    if failure == 'exception':
        manager.workflow = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('private secret'))
    else:
        setup['result'].update(status='unknown')
    setup['tasks'].pop()()
    done = manager.get(run['id'])
    assert done['status'] == 'unknown'
    assert done['items'][1] == previous_second
    assert 'private secret' not in json.dumps(done)


@pytest.mark.parametrize('record_name,field,value', [
    ('trial','article_id',2), ('trial','configuration_id',1), ('trial','model','gpt-other'),
    ('trial','status','incomplete'), ('trial','run_key','foreign.generation'),
    ('assessment','article_id',2), ('assessment','configuration_id',1),
    ('assessment','model','gpt-other'), ('assessment','status','failed'),
    ('assessment','run_key','foreign.assessment'),
])
def test_mismatched_saved_records_cannot_resume(setup, monkeypatch, record_name, field, value):
    manager, run, trial, assessment, _ = blocked_run(setup, monkeypatch)
    (trial if record_name == 'trial' else assessment)[field] = value
    with pytest.raises(ValueError, match='no corresponden'):
        manager.resume_saved(run['id'])
    assert not setup['tasks'] and not setup['calls']
    assert manager.get(run['id'])['items'] == run['items']


@pytest.mark.parametrize('name,error', [('get_trial',KeyError('missing')), ('get_trial',ValueError('deleted')), ('get_assessment',KeyError('missing'))])
def test_missing_or_deleted_output_never_creates_new_generation(setup, monkeypatch, name, error):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch)
    monkeypatch.setattr(generation_jobs, name, lambda *args: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):
        manager.resume_saved(run['id'])
    assert not setup['tasks'] and not setup['calls'] and not setup['validations']


def test_stale_context_blocks_recovery_without_regenerating(setup, monkeypatch):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch)
    original = generation_jobs._preflight
    def stale(*args):
        status, target = original(*args)
        status['context_fingerprint'] = 'changed'
        return status, target
    monkeypatch.setattr(generation_jobs, '_preflight', stale)
    with pytest.raises(ValueError, match='cambió'):
        manager.resume_saved(run['id'])
    assert not setup['tasks'] and not setup['calls']


def test_another_active_job_prevents_recovery(setup, monkeypatch):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch)
    manager.submit(body(request_key='fixture-operation-2'))
    task_count = len(setup['tasks'])
    with pytest.raises(JobBusyError):
        manager.resume_saved(run['id'])
    assert len(setup['tasks']) == task_count
    assert manager.get(run['id'])['status'] == 'partial'


@pytest.mark.parametrize('result_override', [
    {'generation_status':'incomplete'}, {'stage':'image'}, {'trial_id':None},
    {'assessment_id':None}, {'semantic_validation':{'valid':True}},
])
def test_nonrecoverable_shapes_never_show_or_start_recovery(setup, monkeypatch, result_override):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch)
    items = run['items']
    items[0]['result'].update(result_override)
    manager._save(run['id'], 'partial', items)
    assert not manager.get(run['id'])['can_resume_saved']
    with pytest.raises(ValueError):
        manager.resume_saved(run['id'])
    assert not setup['tasks'] and not setup['calls']


def recovery_app(setup, monkeypatch):
    manager, run, _, _, _ = blocked_run(setup, monkeypatch)
    app = create_app(setup['path'], test_config={'TESTING':True, 'GENERATION_ENV_PATH':str(setup['env']), 'DEPLOYMENT_ENV_PATH':str(setup['env'])})
    active = app.extensions['generation_jobs']
    active.runner = setup['tasks'].append
    active.workflow = setup['workflow']
    active.model_validator = setup['validator']
    return app, app.test_client(), run


def test_resume_route_requires_token_and_same_origin(setup, monkeypatch):
    app, client, run = recovery_app(setup, monkeypatch)
    path = f"/api/generation/runs/{run['id']}/resume"
    assert client.post(path,json={}).status_code == 403
    assert client.post(path,json={},headers={'X-Panel-Token':app.config['PANEL_TOKEN'],'Origin':'https://external.example'}).status_code == 403
    assert not setup['tasks'] and not setup['calls']
    result = client.post(path,json={},headers={'X-Panel-Token':app.config['PANEL_TOKEN']})
    assert result.status_code == 202 and result.json['status'] == 'queued'
    assert len(setup['tasks']) == 1 and not setup['validations']


@pytest.mark.parametrize('payload', [None, [], {'model':'gpt-other'}, {'send_draft':True}, {'new_generation':True}])
def test_resume_route_cannot_alter_original_selection_or_options(setup, monkeypatch, payload):
    app, client, run = recovery_app(setup, monkeypatch)
    response = client.post(f"/api/generation/runs/{run['id']}/resume",json=payload,headers={'X-Panel-Token':app.config['PANEL_TOKEN']})
    assert response.status_code == 400
    assert not setup['tasks'] and not setup['calls']
