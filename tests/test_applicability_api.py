"""The panel saves explicit, version-pinned reviews without any provider traffic."""
from contextlib import closing
from dataclasses import replace
import copy
import socket
import sqlite3

import pytest
import requests

from src.dashboard.app import create_app
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult


@pytest.fixture
def panel(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        pytest.fail('Applicability review must not contact an external service')

    monkeypatch.setattr(socket, 'create_connection', forbid_network)
    monkeypatch.setattr(requests.Session, 'request', forbid_network)
    path = tmp_path / 'articles.db'
    env = tmp_path / '.env'
    env.write_text('', encoding='utf-8')
    article = ExtractionResult(
        url='https://movingtospain.com/renting/', canonical_url='https://movingtospain.com/renting/',
        title='Renting in Spain', original_text='Unneeded author attribution',
        blocks=[{'key': 'p1', 'kind': 'paragraph', 'position': 0, 'source_order': 1,
                 'text': 'Rental costs depend on location and property size.', 'data': {}, 'flags': []}],
    )
    with Database(path) as db:
        db.store_article(article, raw_html='<p>Local source fixture</p>')
    app = create_app(path, test_config={'TESTING': True, 'GENERATION_ENV_PATH': str(env),
                                      'DEPLOYMENT_ENV_PATH': str(env)})
    return app.test_client(), {'X-Panel-Token': app.config['PANEL_TOKEN']}, path, article


def status(panel, configuration=3):
    return panel[0].get(f'/api/generation/applicability?article_id=1&configuration_id={configuration}')


def review(decision='needs_verification'):
    return {
        'decision': decision, 'central_question': '¿Cómo alquilar una vivienda en España?',
        'reason': 'Falta comprobar el alcance del apartado de requisitos.', 'reviewer': 'Editor de prueba',
        'excluded_sections': [], 'evidence': [],
        'checks': {'central_applicable': None, 'whole_article_exclusive': None,
                   'alternative_applicable': None, 'essential_information_verified': False},
    }


def body(panel, decision='needs_verification'):
    return {'article_id': 1, 'configuration_id': 3,
            'context_fingerprint': status(panel).json['context_fingerprint'], 'review': review(decision)}


def post(panel, payload):
    return panel[0].post('/api/generation/applicability', json=payload, headers=panel[1])


def test_read_does_not_create_an_assessment_or_database_table(panel):
    response = status(panel)
    assert response.status_code == 200
    assert response.json['state'] == 'not_reviewed'
    assert response.json['allowed'] is False
    assert response.json['latest_review'] is None
    assert response.json['context']['article_title'] == 'Renting in Spain'
    with closing(sqlite3.connect(panel[2])) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='generation_applicability_reviews'").fetchone()
    page = panel[0].get('/').get_data(as_text=True)
    assert 'id="applicability-review-form"' in page and 'applicability.js' in page
    assert 'applicability.css' in page


def test_pending_and_approved_reviews_are_separate_historical_records(panel):
    pending = post(panel, body(panel))
    assert pending.status_code == 201
    assert pending.json['review']['source'] == 'manual'
    assert pending.json['status']['state'] == 'current'
    assert pending.json['status']['allowed'] is False
    assert 'not_automatically_verified' in pending.json['review']['evidence_verification']
    approved = body(panel, 'adapt')
    approved['review']['checks'].update(central_applicable=True, whole_article_exclusive=False,
                                      essential_information_verified=True)
    approved['review']['excluded_sections'] = [{'section': 'US allowance', 'reason': 'Exclusive to a different audience.'}]
    saved = post(panel, approved)
    assert saved.status_code == 201 and saved.json['status']['allowed']
    assert saved.json['review']['excluded_sections'] == approved['review']['excluded_sections']
    assert status(panel).json['latest_review']['id'] == saved.json['review']['id']
    assert status(panel, 1).json['state'] == 'not_reviewed'
    with closing(sqlite3.connect(panel[2])) as db:
        assert db.execute('SELECT count(*) FROM generation_applicability_reviews').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM generation_preparations').fetchone()[0] == 0


def test_omission_needs_evidence_and_confirmed_entire_exclusivity(panel):
    payload = body(panel, 'omit')
    assert post(panel, payload).status_code == 400
    payload['review']['checks'].update(central_applicable=False, whole_article_exclusive=True,
                                     alternative_applicable=False, essential_information_verified=True)
    assert post(panel, payload).status_code == 400
    payload['review']['evidence'] = [{'url': 'https://example.gov/fixture-rule', 'checked_at': '2026-01-01',
                                    'summary': 'Test-only evidence describing the exclusivity and lack of alternative.'}]
    response = post(panel, payload)
    assert response.status_code == 201
    assert response.json['review']['decision'] == 'omit' and not response.json['status']['allowed']


def test_changed_source_rejects_old_form_and_reports_stale_review(panel):
    payload = body(panel)
    assert post(panel, payload).status_code == 201
    article = replace(panel[3], title='Updated rental information', blocks=[{
        'key': 'p2', 'kind': 'paragraph', 'position': 0, 'source_order': 1,
        'text': 'The source article now covers a different requirement.', 'data': {}, 'flags': [],
    }])
    with Database(panel[2]) as db:
        db.store_article(article, raw_html='<p>Updated fixture</p>')
    rejected = post(panel, payload)
    assert rejected.status_code == 400 and 'cambiaron' in rejected.json['error']
    current = status(panel).json
    assert current['state'] == 'stale' and current['current_review'] is None
    assert current['latest_review']['decision'] == 'needs_verification'
    assert post(panel, body(panel)).status_code == 201
    assert status(panel).json['state'] == 'current'


@pytest.mark.parametrize('query', [
    '', '?article_id=1', '?article_id=1&configuration_id=true',
    '?article_id=-1&configuration_id=3', '?article_id=1.5&configuration_id=3',
    '?article_id=9223372036854775808&configuration_id=3',
    '?article_id=1&article_id=2&configuration_id=3',
    '?article_id=1&configuration_id=3&decision=adapt',
])
def test_invalid_or_ambiguous_lookup_rejected(panel, query):
    assert panel[0].get('/api/generation/applicability' + query).status_code == 400


@pytest.mark.parametrize('key,value', [
    ('article_id', True), ('configuration_id', '3'), ('article_id', 0),
    ('context_fingerprint', ''), ('context_fingerprint', None),
    ('context_fingerprint', 'a' * 63), ('unexpected', True), ('review', []),
])
def test_strict_body_cannot_bypass_review_context(panel, key, value):
    payload = body(panel)
    payload[key] = value
    assert post(panel, payload).status_code == 400
    assert status(panel).json['state'] == 'not_reviewed'


def test_review_cannot_claim_adapt_without_essential_checks(panel):
    payload = body(panel, 'adapt')
    response = post(panel, payload)
    assert response.status_code == 400
    assert status(panel).json['state'] == 'not_reviewed'


def test_routes_require_existing_pair_json_and_local_session(panel):
    payload = body(panel)
    assert panel[0].post('/api/generation/applicability', json=payload).status_code == 403
    assert panel[0].post('/api/generation/applicability', json=payload,
                         headers=panel[1] | {'Origin': 'https://other.example'}).status_code == 403
    assert panel[0].post('/api/generation/applicability', data='plain text', headers=panel[1]).status_code == 400
    assert post(panel, []).status_code == 400
    missing = copy.deepcopy(payload)
    del missing['context_fingerprint']
    assert post(panel, missing).status_code == 400
    assert status(panel, 999).status_code == 404
    assert post(panel, payload | {'article_id': 999}).status_code == 404
