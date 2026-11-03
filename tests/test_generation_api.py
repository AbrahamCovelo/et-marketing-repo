"""Integration of the preparation UI endpoints with zero provider traffic."""
from dataclasses import replace
import json
import socket
import sqlite3

import pytest
import requests

from src.dashboard.app import create_app
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult


@pytest.fixture
def generation_panel(tmp_path, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError('Generation preparation must never contact the network')
    monkeypatch.setattr(socket, 'create_connection', reject_network)
    monkeypatch.setattr(requests.Session, 'request', reject_network)
    monkeypatch.setenv('GENERATION_ENABLED', 'true')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    env = tmp_path / '.env'
    env.write_text('OPENAI_API_KEY=private-fixture-secret\n', encoding='utf-8')
    path = tmp_path / 'corpus.db'
    article = ExtractionResult(
        url='https://movingtospain.com/example/', canonical_url='https://movingtospain.com/example/',
        title='Source article', published_at='2026-01-01T00:00:00Z',
        metadata={'authors': ['Private Original Author']}, original_text='Original discarded anecdote.',
        blocks=[{'key': 'b1', 'kind': 'paragraph', 'position': 0, 'source_order': 1,
                 'text': 'Useful retained source text for later adaptation.', 'data': {}, 'flags': []}],
        events=[{'reason': 'personal_experience', 'text': 'Original discarded anecdote.', 'requires_review': True}],
    )
    with Database(path) as db:
        db.store_article(article, raw_html='<html>local fixture</html>')
        db.store_article(replace(article, url='https://movingtospain.com/second/',
                                 canonical_url='https://movingtospain.com/second/', title='Second article'), raw_html='<html>second</html>')
    app = create_app(path, test_config={'TESTING': True, 'GENERATION_ENV_PATH': str(env)})
    return app, app.test_client(), path, article


def mutate(panel, url, payload, method='POST'):
    app, client, _, _ = panel
    return client.open(url, method=method, json=payload, headers={'X-Panel-Token': app.config['PANEL_TOKEN']})


def test_defaults_are_three_variants_empty_drafts_and_no_secret(generation_panel):
    _, client, path, _ = generation_panel
    response = client.get('/api/generation/catalog')
    assert response.status_code == 200
    data = response.json
    assert data['mode'] == 'preparation'
    assert data['execution_enabled'] is False
    assert {d['locale'] for d in data['destinations']} == {'en-CA', 'fr-CA', 'es-CO'}
    assert len({d['site_url'] for d in data['destinations']}) == 2
    assert len(data['prompts']) == len(data['configurations']) == 3
    assert all(p['status'] == 'draft' and not p['system_prompt'] and not p['template'] for p in data['prompts'])
    assert {'provider': 'openai', 'configured': True} in data['credentials']
    assert 'private-fixture-secret' not in response.get_data(as_text=True)
    with sqlite3.connect(path) as db:
        assert not any('private-fixture-secret' in line for line in db.iterdump())


def test_preview_renders_only_local_sanitized_source_without_plans(generation_panel):
    _, client, path, _ = generation_panel
    response = mutate(generation_panel, '/api/generation/preview', {'article_ids': [1, 2], 'configuration_ids': [1, 2, 3]})
    assert response.status_code == 200
    assert response.json['pair_count'] == 6
    assert len(response.json['items']) == 6
    assert response.json['example']['source_context']['content']['clean_text'].startswith('Useful retained')
    assert response.json['example']['rendered_prompt'] == ''
    assert response.json['warnings']
    assert 'Original discarded anecdote' not in response.get_data(as_text=True)
    assert 'Private Original Author' not in response.get_data(as_text=True)
    assert client.get('/api/generation/plans').json['items'] == []
    with sqlite3.connect(path) as db:
        assert db.execute('select count(*) from generation_preparations').fetchone()[0] == 0


def test_preparing_deduplicating_and_cancelling_never_marks_adapted(generation_panel):
    _, client, _, _ = generation_panel
    body = {'article_ids': [1], 'configuration_ids': [1, 2, 3]}
    first = mutate(generation_panel, '/api/generation/plans', body)
    assert first.status_code == 201
    assert first.json['created_count'] == 3
    second = mutate(generation_panel, '/api/generation/plans', body)
    assert second.status_code == 201
    assert second.json['created_count'] == 0 and second.json['reused_count'] == 3
    history = client.get('/api/articles/1/adaptations').json
    assert history['generated_count'] == 0 and history['prepared_count'] == 3
    assert history['message'] == 'Todavía no hubo adaptaciones.'
    listed = next(item for item in client.get('/api/articles?q=Source').json['items'] if item['id'] == 1)
    assert listed['adaptation_status'] == 'not_adapted'
    assert listed['generated_count'] == 0
    assert listed['prepared_count'] == 3
    assert client.get('/api/articles/1').json['adaptations']['prepared_count'] == 3
    assert mutate(generation_panel, f"/api/generation/plans/{first.json['id']}/cancel", {}).status_code == 200
    assert client.get('/api/articles/1/adaptations').json['prepared_count'] == 3
    assert mutate(generation_panel, f"/api/generation/plans/{second.json['id']}/cancel", {}).status_code == 200
    history = client.get('/api/articles/1/adaptations').json
    assert history['prepared_count'] == 0 and history['generated_count'] == 0


def test_execution_cannot_be_enabled_by_payload_or_environment(generation_panel):
    _, client, path, _ = generation_panel
    for payload in ({}, {'enabled': True, 'generation_enabled': True, 'model': 'anything', 'api_key': 'not-used'}):
        response = mutate(generation_panel, '/api/generation/execute', payload)
        assert response.status_code == 409
        assert response.json['execution_enabled'] is False
        assert 'deshabilitada' in response.json['error']
    assert client.get('/api/generation/plans').json['items'] == []
    with sqlite3.connect(path) as db:
        assert db.execute('select count(*) from crawl_runs').fetchone()[0] == 0


def test_crud_pins_versions_and_reports_changed_sources(generation_panel):
    _, client, path, article = generation_panel
    prompt = {'name': 'Template to review', 'description': '', 'system_prompt': 'Use the supplied source.',
              'template': 'Destination: {{locale}}\n{{article_json}}', 'status': 'reviewed', 'archived': False}
    first = mutate(generation_panel, '/api/generation/prompts', prompt)
    assert first.status_code == 201
    config = {'name': 'Test configuration', 'destination_id': 1, 'prompt_version_id': first.json['current_version_id'],
              'provider': 'openai', 'model': '', 'notes': '', 'enabled': True}
    saved = mutate(generation_panel, '/api/generation/configurations', config)
    assert saved.status_code == 201
    configuration_id = saved.json['id']
    plan = mutate(generation_panel, '/api/generation/plans', {'article_ids': [1], 'configuration_ids': [configuration_id]}).json
    new_prompt = mutate(generation_panel, f"/api/generation/prompts/{first.json['id']}", {**prompt, 'system_prompt': 'Revised instructions.'}, method='PUT')
    assert new_prompt.status_code == 200
    current = next(c for c in client.get('/api/generation/catalog').json['configurations'] if c['id'] == configuration_id)
    assert current['prompt_version_id'] == first.json['current_version_id']
    assert current['newer_prompt_available'] is True
    pinned = client.get(f'/api/generation/configurations/{configuration_id}')
    assert pinned.status_code == 200
    assert pinned.json['prompt']['system_prompt'] == prompt['system_prompt']
    assert pinned.json['prompt']['version_id'] == first.json['current_version_id']
    updated = mutate(generation_panel, f'/api/generation/configurations/{configuration_id}',
                     {**config, 'prompt_version_id': new_prompt.json['current_version_id']}, method='PUT')
    assert updated.status_code == 200
    with Database(path) as db:
        new_blocks = [{**article.blocks[0], 'text': 'Changed source text for a new source version.'}]
        db.store_article(replace(article, blocks=new_blocks), raw_html='<html>new version</html>')
    historic = client.get(f"/api/generation/plans/{plan['id']}").json['items'][0]
    assert historic['source_changed'] and historic['configuration_changed']
    assert historic['prompt_version_id'] == first.json['current_version_id']
    assert historic['source_version_id'] == plan['items'][0]['source_version_id']
    assert client.get('/api/articles/1/adaptations').json['generated_count'] == 0


def test_invalid_batch_is_atomic_and_actions_require_token(generation_panel):
    _, client, path, _ = generation_panel
    body = {'article_ids': [1, 999], 'configuration_ids': [1]}
    assert client.post('/api/generation/plans', json=body).status_code == 403
    assert mutate(generation_panel, '/api/generation/plans', body).status_code == 404
    assert client.get('/api/generation/plans').json['items'] == []
    assert mutate(generation_panel, '/api/generation/preview', {'article_ids': [True], 'configuration_ids': [1]}).status_code == 400
    assert mutate(generation_panel, '/api/generation/plans', {**body, 'execute': True}).status_code == 400
    assert client.get('/api/generation/plans?limit=500').status_code == 400
    assert client.get('/api/generation/plans/999').status_code == 404
    assert client.get('/api/generation/configurations/999').status_code == 404
    with sqlite3.connect(path) as db:
        assert not db.execute('pragma foreign_key_check').fetchall()
