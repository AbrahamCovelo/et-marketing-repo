"""Panel management uses saved content, with no generation or real network calls."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from test_deployment_trial import saved_trial, Session, send, SECRET
from src.dashboard.app import create_app
from src.deployment.management import (
    AdaptationConflict, delete_adaptation, initialize_management,
    preview_redeployment, redeploy_adaptation,
)
from src.generation.repository import GenerationRepository
from src.generation.trial import get_trial, run_trial


@pytest.fixture
def deployed(saved_trial, monkeypatch):
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: pytest.fail('Unexpected real HTTP'))
    send(saved_trial, Session())
    initialize_management(saved_trial[0])
    return saved_trial


class Remote(Session):
    def __init__(self, *, remote_status='draft', post_http=200, post_error='rest_post_invalid_id', **kwargs):
        super().__init__(**kwargs)
        self.remote = {'id': 1510, 'status': remote_status, 'modified_gmt': '2026-11-05T00:00:00',
                       'content': {'raw': 'Remote content'}, 'title': {'raw': 'Remote title'},
                       'featured_media': 1369, 'categories': [19, 23]}
        self.post_body = {'id': 1510, 'status': remote_status, 'featured_media': 1369, 'categories': [19, 23]}
        self.post_http = post_http
        self.post_error = post_error

    def request(self, method, url, **kwargs):
        route = kwargs['params']['rest_route']
        if method == 'GET' and '/posts/' in route:
            self.calls.append((method, url, kwargs))
            body = self.remote if self.post_http == 200 else {'code': self.post_error}
            return SimpleNamespace(status_code=self.post_http, json=lambda:body)
        return super().request(method, url, **kwargs)


def preview(deployed, remote):
    path, env = deployed
    return preview_redeployment(path, 1, env_path=env, session=remote)


def execute(deployed, remote, checked, key='unique-operation-1', **kwargs):
    path, env = deployed
    return redeploy_adaptation(path, 1, env_path=env, session=remote,
                              request_key=key, revision=checked['revision'], **kwargs)


def writes(remote):
    return [call for call in remote.calls if call[0] == 'POST']


def test_preview_read_only_then_update_same_post_without_duplicate(deployed):
    remote = Remote()
    checked = preview(deployed, remote)
    assert checked['mode'] == 'update' and checked['post_id'] == 1510
    assert not writes(remote)
    result = execute(deployed, remote, checked)
    assert result['status'] == 'completed' and result['result']['post_id'] == 1510
    post = writes(remote)[0]
    assert post[2]['params']['rest_route'] == '/wp/v2/posts/1510'
    assert 'status' not in post[2]['json'] and 'slug' not in post[2]['json']
    assert post[2]['json']['content'] == get_trial(deployed[0], 1)['html']
    assert post[2]['allow_redirects'] is False and post[1] == 'https://colombianosespana.com/'
    calls = len(remote.calls)
    assert execute(deployed, remote, checked) == result
    assert len(remote.calls) == calls
    item = GenerationRepository(deployed[0]).trial_detail(1)
    assert item['deployment']['status'] == 'draft' and item['deployment']['post_id'] == 1510


def test_missing_post_creates_only_draft_and_next_preview_uses_new_id(deployed):
    remote = Remote(post_http=404)
    remote.post_body = {'id': 1600, 'status': 'draft', 'featured_media': 1369, 'categories': [19, 23]}
    checked = preview(deployed, remote)
    assert checked['mode'] == 'create' and checked['post_id'] is None
    assert execute(deployed, remote, checked)['result']['post_id'] == 1600
    assert writes(remote)[0][2]['params']['rest_route'] == '/wp/v2/posts'
    assert writes(remote)[0][2]['json']['status'] == 'draft'
    remote.post_http = 200
    remote.remote['id'] = 1600
    next_preview = preview(deployed, remote)
    assert next_preview['mode'] == 'update' and next_preview['post_id'] == 1600
    assert any(call[2]['params']['rest_route'] == '/wp/v2/posts/1600' for call in remote.calls)


@pytest.mark.parametrize('status,code', [(401, 'rest_not_logged_in'), (403, 'rest_forbidden'), (500, 'error'), (404, 'rest_no_route')])
def test_permission_or_route_failures_do_not_create_a_new_post(deployed, status, code):
    remote = Remote(post_http=status, post_error=code)
    with pytest.raises(ValueError):
        preview(deployed, remote)
    assert not writes(remote)


def test_post_changed_since_confirmation_rejects_write(deployed):
    remote = Remote()
    checked = preview(deployed, remote)
    remote.remote['content']['raw'] = 'Someone edited this in WordPress'
    with pytest.raises(AdaptationConflict, match='cambiaron'):
        execute(deployed, remote, checked)
    assert not writes(remote)


def test_pending_ctas_need_explicit_confirmation_and_stay_in_draft(deployed):
    with sqlite3.connect(deployed[0]) as db:
        db.execute("UPDATE generation_trials SET html='<p>Visa [CTA_INMIGRACION_ES]</p>'")
    remote = Remote()
    checked = preview(deployed, remote)
    assert checked['pending_ctas']
    with pytest.raises(ValueError, match='CTA'):
        execute(deployed, remote, checked)
    assert not writes(remote)
    assert execute(deployed, remote, checked, allow_pending_ctas=True)['status'] == 'completed'


def test_published_update_requires_confirmation_and_preserves_status(deployed):
    remote = Remote(remote_status='publish')
    checked = preview(deployed, remote)
    assert checked['live_update']
    with pytest.raises(ValueError, match='publicada'):
        execute(deployed, remote, checked)
    assert not writes(remote)
    result = execute(deployed, remote, checked, confirm_live=True)
    assert result['result']['post_status'] == 'publish'
    assert 'status' not in writes(remote)[0][2]['json']
    assert GenerationRepository(deployed[0]).trial_detail(1)['deployment']['status'] == 'publish'


def test_pending_ctas_never_reach_published_post(deployed):
    with sqlite3.connect(deployed[0]) as db:
        db.execute("UPDATE generation_trials SET html='<p>[CTA_INMIGRACION_ES]</p>'")
    with pytest.raises(ValueError, match='CTA'):
        preview(deployed, Remote(remote_status='publish'))


def test_trashed_post_requires_manual_restore_not_duplicate(deployed):
    remote = Remote(remote_status='trash')
    with pytest.raises(ValueError, match='papelera'):
        preview(deployed, remote)
    assert not writes(remote)


def test_timeout_is_durable_and_blocks_new_attempt_and_delete(deployed):
    remote = Remote(exception=requests.Timeout(SECRET))
    checked = preview(deployed, remote)
    result = execute(deployed, remote, checked)
    assert result['status'] == 'unknown' and SECRET not in json.dumps(result)
    before = len(remote.calls)
    assert execute(deployed, remote, checked) == result
    assert before == len(remote.calls)
    with pytest.raises(AdaptationConflict):
        execute(deployed, remote, checked, key='different-operation-2')
    with pytest.raises(AdaptationConflict):
        delete_adaptation(deployed[0], 1)
    assert len(writes(remote)) == 1


def test_failed_update_can_be_explicitly_retried_with_known_post(deployed):
    remote = Remote(status=403)
    checked = preview(deployed, remote)
    assert execute(deployed, remote, checked)['status'] == 'failed'
    history = GenerationRepository(deployed[0]).trial_detail(1)['deployment']
    assert history['status'] == 'failed' and history['post_id'] == 1510
    remote.status = 200
    checked = preview(deployed, remote)
    assert execute(deployed, remote, checked, key='different-operation-2')['status'] == 'completed'
    assert len(writes(remote)) == 2


def test_reserved_operation_blocks_delete_during_post(deployed):
    def during_post():
        with sqlite3.connect(deployed[0]) as db:
            assert db.execute('SELECT status FROM adaptation_actions').fetchone()[0] == 'running'
        with pytest.raises(AdaptationConflict):
            delete_adaptation(deployed[0], 1)
    remote = Remote(before_post=during_post)
    assert execute(deployed, remote, preview(deployed, remote))['status'] == 'completed'


def test_delete_purges_all_database_copies_but_preserves_source_and_history(deployed):
    path, env = deployed
    remote = Remote()
    execute(deployed, remote, preview(deployed, remote))
    with sqlite3.connect(path) as db:
        sources = db.execute('SELECT * FROM article_versions').fetchall()
        db.execute("UPDATE generation_trials SET response_json=?,snapshot_json=?,usage_json=?",
                   ('{"output":"ADAPTED_TEXT"}', '{"data":"ADAPTED_TEXT"}', '{"total_tokens":123}'))
    calls = len(remote.calls)
    assert delete_adaptation(path, 1)['deleted']
    assert len(remote.calls) == calls
    with sqlite3.connect(path) as db:
        assert sources == db.execute('SELECT * FROM article_versions').fetchall()
        assert db.execute('SELECT html,response_json,snapshot_json,usage_json FROM generation_trials').fetchone() == ('', '{}', '{}', '{"total_tokens":123}')
        assert db.execute('SELECT payload_json FROM deployment_trials').fetchone()[0] == '{}'
        assert db.execute('SELECT payload_json FROM adaptation_actions').fetchone()[0] == '{}'
    history = GenerationRepository(path).article_history(1)
    assert history['generated_count'] == 0 and history['trials'] == []
    assert history['message'] == 'Todavía no hubo adaptaciones.'
    with pytest.raises(KeyError):
        GenerationRepository(path).trial_detail(1)
    assert GenerationRepository(path).article_summaries([1])[1]['generated_count'] == 0
    assert delete_adaptation(path, 1)['deleted']
    with pytest.raises(ValueError, match='eliminado'):
        get_trial(path, 1)
    with pytest.raises(ValueError, match='eliminada'):
        run_trial(path,1,3,'gpt-test','fixture',env_path=env)
    with pytest.raises(ValueError, match='eliminado'):
        preview(deployed, remote)


def test_delete_after_preview_prevents_send(deployed):
    remote = Remote()
    checked = preview(deployed, remote)
    delete_adaptation(deployed[0], 1)
    with pytest.raises(ValueError, match='eliminado'):
        execute(deployed, remote, checked)
    assert not writes(remote)


@pytest.mark.parametrize('status', ['running', 'unknown'])
def test_unresolved_generation_cannot_be_deleted(deployed, status):
    with sqlite3.connect(deployed[0]) as db:
        db.execute('UPDATE generation_trials SET status=?', (status,))
    with pytest.raises(AdaptationConflict):
        delete_adaptation(deployed[0], 1)


def test_api_requires_csrf_and_explicit_boolean_and_hides_deleted_text(deployed):
    path, env = deployed
    app = create_app(path, test_config={'TESTING': True, 'DEPLOYMENT_ENV_PATH': str(env)})
    client = app.test_client()
    url = '/api/generation/trials/1'
    assert client.delete(url, json={'confirm': True}).status_code == 403
    headers = {'X-Panel-Token': app.config['PANEL_TOKEN']}
    assert client.delete(url, json={'confirm': 1}, headers=headers).status_code == 400
    assert client.delete(url, json={'confirm': True}, headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.delete(url, json={'confirm': True}, headers=headers).status_code == 200
    assert client.get(url).status_code == 404
    assert client.get('/api/articles/1').json['adaptations']['generated_count'] == 0
    assert client.post('/api/generation/execute', json={}, headers=headers).status_code == 409
    assert client.post('/api/deployment/execute', json={}, headers=headers).status_code == 409


def test_api_preview_and_send_use_private_transport_and_return_safe_fields(deployed, monkeypatch):
    path, env = deployed
    remote = Remote()
    monkeypatch.setattr(requests.Session, 'request', lambda self,*a,**kw:remote.request(*a,**kw))
    app = create_app(path, test_config={'TESTING': True, 'DEPLOYMENT_ENV_PATH': str(env)})
    client = app.test_client()
    headers = {'X-Panel-Token': app.config['PANEL_TOKEN']}
    url = '/api/generation/trials/1'
    assert client.post(url+'/redeploy-preview', json={}).status_code == 403
    checked = client.post(url+'/redeploy-preview', json={}, headers=headers).json
    response = client.post(url+'/redeploy', json={'request_key':'api-operation-123456','revision':checked['revision'],
                                                'allow_pending_ctas':False,'confirm_live':False}, headers=headers)
    assert response.status_code == 200 and response.json['status'] == 'completed'
    assert SECRET not in response.get_data(as_text=True)
    assert 'payload' not in response.json
    with sqlite3.connect(path) as db:
        assert SECRET not in '\n'.join(db.iterdump())
