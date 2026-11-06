"""Editorial revisions are local, validated and distinct from model output."""
import json
import sqlite3
from pathlib import Path

import pytest
import requests

from test_adaptation_management import deployed, Remote, preview, execute, writes
from test_deployment_trial import saved_trial, Session, send
from scripts.run_generation_trial import export_trial
from src.dashboard.app import create_app
from src.deployment.management import AdaptationConflict, delete_adaptation, save_adaptation_content
from src.generation.repository import GenerationRepository
from src.generation.trial import get_trial, list_trials


EDITED = '<h2>Preparación del expediente</h2><p>Texto corregido por el editor.</p>'


def save(path, html=EDITED, revision=None):
    return save_adaptation_content(path, 1, html=html,
                                   revision=revision or get_trial(path, 1)['content_revision'])


def test_save_preserves_model_output_and_updates_effective_reads_and_export(deployed, tmp_path):
    path, _ = deployed
    before = get_trial(path, 1)
    result = save(path)
    current = get_trial(path, 1)
    assert result['html'] == current['html'] == EDITED
    assert current['original_html'] == before['html']
    assert current['response'] == before['response']
    assert current['html_source'] == 'editor' and current['edit_version'] == 1
    assert current['content_revision'] != before['content_revision']
    assert GenerationRepository(path).trial_detail(1)['html'] == EDITED
    history = GenerationRepository(path).article_history(1)
    assert history['trials'][0]['edited_at'] and history['trials'][0]['edit_version'] == 1
    assert history['generated_count'] == 1
    assert list_trials(path)[0]['html'] == EDITED
    exported = export_trial(current, title='Prueba editorial', output_dir=tmp_path / 'export')
    assert Path(exported['artifacts']['body']).read_text(encoding='utf-8') == EDITED
    metadata = json.loads(Path(exported['artifacts']['metadata']).read_text(encoding='utf-8'))
    assert metadata['html_source'] == 'editor' and metadata['edit_version'] == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT html FROM generation_trials').fetchone()[0] == before['html']


def test_edits_versioned_noop_deduplicated_and_stale_save_rejected(deployed):
    path, _ = deployed
    old = get_trial(path, 1)['content_revision']
    first = save(path)
    assert save(path)['content_revision'] == first['content_revision']
    with pytest.raises(AdaptationConflict, match='cambió'):
        save(path, '<p>Stale overwrite.</p>', revision=old)
    newer = save(path, '<p>Segunda edición.</p>')
    assert newer['edit_version'] == 2
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version,html FROM adaptation_edits ORDER BY version').fetchall() == [
            (1, EDITED), (2, '<p>Segunda edición.</p>')]


@pytest.mark.parametrize('html', [
    '<p onclick="alert(1)">Texto</p>', '<script>alert(1)</script>',
    '<p><a href="javascript:alert(1)">Texto</a></p>', '<p style="color:red">Texto</p>',
    '', None, '<p>Texto [CTA_TAXES_EN]</p>',
])
def test_invalid_or_wrong_locale_edits_do_not_change_content(deployed, html):
    path, _ = deployed
    before = get_trial(path, 1)
    with pytest.raises(ValueError):
        save(path, html)
    assert get_trial(path, 1)['html'] == before['html']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM adaptation_edits').fetchone()[0] == 0


def test_cta_placeholders_allowed_for_same_locale(deployed):
    assert save(deployed[0], '<p>Visados [CTA_INMIGRACION_ES]</p>')['edit_version'] == 1


def test_redeployment_sends_saved_edit_without_another_model_request(deployed):
    path, _ = deployed
    save(path)
    remote = Remote()
    result = execute(deployed, remote, preview(deployed, remote))
    assert result['status'] == 'completed'
    assert writes(remote)[0][2]['json']['content'] == EDITED


def test_edit_invalidates_previously_checked_redeployment(deployed):
    remote = Remote()
    checked = preview(deployed, remote)
    save(deployed[0])
    with pytest.raises(AdaptationConflict, match='cambiaron'):
        execute(deployed, remote, checked)
    assert not writes(remote)


def test_edit_during_redeployment_check_prevents_stale_post(deployed):
    path, _ = deployed
    remote = Remote()
    checked = preview(deployed, remote)
    request = remote.request
    def change_on_final_check(method, url, **kwargs):
        response = request(method, url, **kwargs)
        if method == 'GET' and kwargs['params']['rest_route'].endswith('/categories'):
            save(path)
        return response
    remote.request = change_on_final_check
    with pytest.raises(AdaptationConflict, match='contenido local cambió'):
        execute(deployed, remote, checked)
    assert not writes(remote)


def test_first_deployment_detects_edit_during_remote_checks(saved_trial):
    path, _ = saved_trial
    remote = Session()
    request = remote.request
    def change_on_final_check(method, url, **kwargs):
        response = request(method, url, **kwargs)
        if method == 'GET' and kwargs['params']['rest_route'].endswith('/categories'):
            save(path)
        return response
    remote.request = change_on_final_check
    with pytest.raises(ValueError, match='cambió durante'):
        send(saved_trial, remote)
    assert not writes(remote)
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'deployment_trials' not in tables or db.execute('SELECT COUNT(*) FROM deployment_trials').fetchone()[0] == 0


def test_first_deployment_reservation_blocks_edits(saved_trial):
    def during_post():
        with pytest.raises(AdaptationConflict, match='envío'):
            save(saved_trial[0])
    result = send(saved_trial, Session(before_post=during_post))
    assert result['status'] == 'draft'


@pytest.mark.parametrize('status', ['running', 'unknown'])
def test_unresolved_delivery_blocks_edits(deployed, status):
    path, _ = deployed
    remote = Remote(exception=requests.Timeout())
    execute(deployed, remote, preview(deployed, remote))
    with sqlite3.connect(path) as db:
        db.execute('UPDATE adaptation_actions SET status=?', (status,))
    with pytest.raises(AdaptationConflict):
        save(path)


def test_delete_removes_all_editor_versions_and_public_remnants(deployed):
    path, _ = deployed
    save(path)
    revision = get_trial(path, 1)['content_revision']
    delete_adaptation(path, 1)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM adaptation_edits').fetchone()[0] == 0
        assert db.execute('SELECT run_key FROM generation_trials').fetchone()[0] == 'fixture'
    assert list_trials(path) == []
    assert GenerationRepository(path).article_history(1)['trials'] == []
    with pytest.raises(KeyError):
        save(path, revision=revision)


def test_editor_api_requires_csrf_revision_and_returns_only_effective_content(deployed):
    path, env = deployed
    app = create_app(path, test_config={'TESTING': True, 'DEPLOYMENT_ENV_PATH': str(env)})
    client = app.test_client()
    url = '/api/generation/trials/1/content'
    headers = {'X-Panel-Token': app.config['PANEL_TOKEN']}
    revision = client.get('/api/generation/trials/1').json['content_revision']
    body = {'html': EDITED, 'revision': revision}
    assert client.put(url, json=body).status_code == 403
    assert client.put(url, json=body, headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.put(url, json={'html': EDITED}, headers=headers).status_code == 400
    response = client.put(url, json=body, headers=headers)
    assert response.status_code == 200 and response.json['html'] == EDITED
    assert response.json['edit_version'] == 1
    assert not {'request', 'response', 'original_html'} & response.json.keys()
    assert client.put(url, json=body, headers=headers).status_code == 409
    client.delete('/api/generation/trials/1', json={'confirm': True}, headers=headers)
    assert client.put(url, json=body, headers=headers).status_code == 404


@pytest.mark.parametrize('status', ['incomplete', 'running', 'unknown', 'failed'])
def test_only_completed_adaptations_can_be_edited(deployed, status):
    path, _ = deployed
    with sqlite3.connect(path) as db:
        db.execute('UPDATE generation_trials SET status=?', (status,))
    with pytest.raises(AdaptationConflict, match='completa'):
        save(path)
