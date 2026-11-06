"""WordPress preparation is local, secret-free and cannot publish."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from src.dashboard.app import create_app
from src.deployment.credentials_import import import_site_credentials
from src.deployment.repository import DeploymentRepository
from src.deployment.settings import CONNECTIONS, credentials_status, load_credentials
from src.deployment.categories import refresh_category_catalog, save_category_policy
from src.generation.repository import GenerationRepository


@pytest.fixture
def panel(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('WordPress preparation must not contact any service')
    monkeypatch.setattr(requests.Session, 'request', no_network)
    monkeypatch.setenv('DEPLOYMENT_ENABLED', 'true')
    env = tmp_path / '.env'
    lines = []
    for prefix, _ in CONNECTIONS.values():
        for suffix in ('_USERNAME', '_APPLICATION_PASSWORD'):
            monkeypatch.delenv(prefix + suffix, raising=False)
        lines += [prefix + '_USERNAME="fixture-user"', prefix + '_APPLICATION_PASSWORD="fixture-private-password"']
    env.write_text('\n'.join(lines), encoding='utf-8')
    path = tmp_path / 'test.db'
    app = create_app(path, test_config={'TESTING': True, 'DEPLOYMENT_ENV_PATH': str(env), 'GENERATION_ENV_PATH': str(env)})
    return app, app.test_client(), path, env


def mutate(panel, url, body, method='POST'):
    app, client, _, _ = panel
    return client.open(url, method=method, json=body, headers={'X-Panel-Token': app.config['PANEL_TOKEN']})


def settings(target):
    return {key: target[key] for key in ('guide_url', 'category_ids', 'language_mode', 'cta_links', 'notes')}


def configure_english(panel):
    _, client, path, env = panel
    target = client.get('/api/deployment/catalog').json['targets'][0]
    body = settings(target)
    body['category_ids'] = [21]
    body['cta_links']['CTA_IMMIGRATION_EN'] = {'url': 'https://canadiansinspain.com/immigration/', 'text': 'Immigration help'}
    response = mutate(panel, f"/api/deployment/targets/{target['id']}", body, 'PUT')
    assert response.status_code == 200
    class CatalogSession:
        def request(self, method, url, **kwargs):
            assert method == 'GET' and url == 'https://canadiansinspain.com/'
            if kwargs['params']['rest_route'].endswith('settings'):
                body = {'default_category': 87}
            else:
                body = [{'id': value, 'name': f'Category {value}', 'slug': f'category-{value}',
                         'description': '', 'parent': 0} for value in (21, 87)]
            return SimpleNamespace(status_code=200, headers={'X-WP-TotalPages': '1'}, json=lambda: body)
    catalog = refresh_category_catalog(path, target['id'], env, CatalogSession())
    save_category_policy(path, target['id'], {'allowed_category_ids': [21], 'excluded_default_category_id': 87,
                                             'catalog_revision': catalog['catalog_revision']})
    return target['id']


def draft(target_id):
    return {'target_id': target_id, 'title': 'A guide', 'html': '<h2>First steps</h2><p>Read [CTA_IMMIGRATION_EN].</p>',
            'excerpt': 'A short guide.', 'featured_media': 42, 'category_ids': [21]}


def test_catalog_has_three_routes_and_only_credential_presence(panel):
    _, client, path, env = panel
    response = client.get('/api/deployment/catalog')
    assert response.status_code == 200
    assert response.json['execution_enabled'] is False
    targets = {item['locale']: item for item in response.json['targets']}
    assert len(targets) == 3
    assert targets['fr-CA']['guide_url'] == '' and targets['fr-CA']['language_mode'] == 'pending'
    assert targets['en-CA']['guide_url'].endswith('/guides-for-canadians-in-spain/')
    assert targets['es-CO']['guide_url'].endswith('/guias-colombianos-en-espana')
    assert targets['fr-CA']['rest_posts_url'] == 'https://canadiansinspain.com/wp-json/wp/v2/posts'
    assert all(t['credentials']['configured'] and not t['credentials']['tested'] for t in targets.values())
    assert 'fixture-private-password' not in response.get_data(as_text=True)
    assert 'fixture-user' not in response.get_data(as_text=True)
    with sqlite3.connect(path) as db:
        dump = '\n'.join(db.iterdump())
        assert 'fixture-private-password' not in dump and 'fixture-user' not in dump
    assert DeploymentRepository(path, env).catalog() == response.json


def test_local_draft_uses_separate_title_body_featured_media_and_no_remote_writes(panel):
    _, client, path, _ = panel
    target_id = configure_english(panel)
    with sqlite3.connect(path) as db:
        before = '\n'.join(db.iterdump())
    response = mutate(panel, '/api/deployment/preview', draft(target_id))
    assert response.status_code == 200 and response.json['ready'] is True
    payload = response.json['payload']
    assert payload['status'] == 'draft' and payload['featured_media'] == 42
    assert payload['categories'] == [21] and 'parent' not in payload
    assert '<a href="https://canadiansinspain.com/immigration/">Immigration help</a>' in payload['content']
    assert '<img' not in payload['content'] and '<h1' not in payload['content']
    assert response.json['warnings'] and response.json['execution_enabled'] is False
    with sqlite3.connect(path) as db:
        assert '\n'.join(db.iterdump()) == before
    blocked = mutate(panel, '/api/deployment/execute', {**payload, 'enabled': True})
    assert blocked.status_code == 409 and blocked.json['execution_enabled'] is False


@pytest.mark.parametrize('changes', [
    {'featured_media': None}, {'title': ''}, {'title': '<h1>Title</h1>'},
    {'html': '<p style="color:red">Text</p>'}, {'html': '<p><img src="https://example.test/a.jpg"></p>'},
    {'html': '<p>Read [CTA_TAXES_EN].</p>'}, {'html': '<p>Lire [CTA_IMMIGRATION_FR].</p>'},
    {'excerpt': '<script>alert(1)</script>'},
])
def test_unresolved_image_ctas_or_nonportable_html_blocks_payload(panel, changes):
    target_id = configure_english(panel)
    response = mutate(panel, '/api/deployment/preview', {**draft(target_id), **changes})
    assert response.status_code == 200 and not response.json['ready']
    assert response.json['payload'] is None and response.json['errors']


def test_french_cannot_be_prepared_as_default_english_language(panel):
    _, client, _, _ = panel
    french = client.get('/api/deployment/catalog').json['targets'][1]
    response = mutate(panel, f"/api/deployment/targets/{french['id']}", {**settings(french), 'language_mode': 'site_default'}, 'PUT')
    assert response.status_code == 400
    preview = mutate(panel, '/api/deployment/preview', {**draft(french['id']), 'html': '<p>Texte.</p>'})
    assert preview.status_code == 200 and not preview.json['ready']


def test_mutable_generation_destination_cannot_change_credential_host_or_bypass_french(panel):
    _, client, path, env = panel
    repo = GenerationRepository(path)
    destination = repo.catalog()['destinations'][1]
    fields = {key: destination[key] for key in ('name', 'site_url', 'locale', 'notes', 'enabled')}
    repo.save_destination({**fields, 'locale': 'en-US', 'site_url': 'https://example.test'}, destination['id'])
    deployment = DeploymentRepository(path, env)
    target = deployment.get_target(2)
    assert target['locale'] == 'fr-CA' and target['destination_locale'] == 'en-US'
    assert target['base_url'] == 'https://canadiansinspain.com'
    assert any('idioma del destino cambió' in error for error in target['blockers'])
    assert any('sitio del destino cambió' in error for error in target['blockers'])
    assert mutate(panel, '/api/deployment/targets/2', {**settings(target), 'language_mode': 'site_default'}, 'PUT').status_code == 400


@pytest.mark.parametrize('changes', [
    {'guide_url': 'https://example.test/guides/'}, {'guide_url': 'http://canadiansinspain.com/guides/'},
    {'guide_url': 'https://user:password@canadiansinspain.com/guides/'},
    {'category_ids': [True]}, {'category_ids': [-1]}, {'language_mode': []},
    {'cta_links': {}}, {'api_key': 'must-not-be-stored'},
])
def test_invalid_target_updates_do_not_persist(panel, changes):
    _, client, _, _ = panel
    target = client.get('/api/deployment/catalog').json['targets'][0]
    response = mutate(panel, '/api/deployment/targets/1', {**settings(target), **changes}, 'PUT')
    assert response.status_code == 400
    assert client.get('/api/deployment/catalog').json['targets'][0] == target


def test_actions_require_token_and_known_targets(panel):
    _, client, _, _ = panel
    assert client.post('/api/deployment/preview', json=draft(1)).status_code == 403
    assert client.post('/api/deployment/execute', json={}).status_code == 403
    assert mutate(panel, '/api/deployment/preview', draft(999)).status_code == 404
    assert mutate(panel, '/api/deployment/preview', {**draft(1), 'publish': True}).status_code == 400
    assert mutate(panel, '/api/deployment/preview', {**draft(1), 'featured_media': True}).status_code == 400


def test_credentials_import_requires_username_preserves_existing_values_and_is_private(tmp_path, monkeypatch):
    sites = tmp_path / 'sites.txt'
    sites.write_text('\n\n'.join(f'Site\nNombre de api: Integration\nAPI key: abcd efgh ijkl mnop qrst uvwx\nURL: {url}' for url in (
        'https://canadiansinspain.com/', 'https://canadiansinspain.com/fr/canadiens-en-espagne/', 'https://colombianosespana.com/')), encoding='utf-8')
    env = tmp_path / '.env'
    env.write_text('OPENAI_API_KEY=keep-this-value\nOTHER_SETTING=keep-too\n', encoding='utf-8')
    with pytest.raises(ValueError):
        import_site_credentials(sites, '', env)
    result = import_site_credentials(sites, 'test-user', env)
    assert result == {'connections_imported': 3, 'tested': False, 'execution_enabled': False}
    for key, (prefix, _) in CONNECTIONS.items():
        monkeypatch.delenv(prefix + '_USERNAME', raising=False)
        monkeypatch.delenv(prefix + '_APPLICATION_PASSWORD', raising=False)
        value = load_credentials(key, env)
        assert value.username == 'test-user' and value.application_password == 'abcd efgh ijkl mnop qrst uvwx'
        assert 'test-user' not in repr(value) and 'abcd' not in repr(value)
        assert credentials_status(key, env)['configured']
    before = env.read_text(encoding='utf-8')
    assert 'OPENAI_API_KEY=keep-this-value' in before and 'OTHER_SETTING=keep-too' in before
    import_site_credentials(sites, 'test-user', env)
    assert env.read_text(encoding='utf-8') == before
    sites.write_text(sites.read_text(encoding='utf-8').replace('colombianosespana.com', 'example.test'), encoding='utf-8')
    with pytest.raises(ValueError):
        import_site_credentials(sites, 'test-user', env)
    assert env.read_text(encoding='utf-8') == before
