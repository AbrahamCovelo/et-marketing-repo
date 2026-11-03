"""HTTP integration for the local data panel; never contacts the source site."""
import json

import pytest

from src.dashboard.app import create_app
from src.dashboard.jobs import JobBusyError
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult


class FakeJobs:
    def __init__(self):
        self.jobs = []
        self.busy = False

    def submit(self, payload):
        if self.busy:
            raise JobBusyError('Ya hay una operación en curso.')
        result = {'id': 'job-1', 'status': 'queued', 'action': payload['action'], 'progress': {}}
        self.jobs.append(result)
        return result

    def list_jobs(self):
        return self.jobs

    def get_job(self, job_id):
        if job_id != 'job-1' or not self.jobs:
            raise KeyError(job_id)
        return self.jobs[0]


@pytest.fixture
def panel(tmp_path):
    path = tmp_path / 'data.db'
    article = ExtractionResult(
        url='https://movingtospain.com/visa-example/',
        canonical_url='https://movingtospain.com/visa-example/',
        title='Guía de visados', language='es', published_at='2026-02-14T09:00:00+00:00',
        modified_at='2026-10-10T12:00:00+00:00',
        metadata={'authors': ['Autor Original']}, original_text='Una experiencia personal descartada.',
        blocks=[{'key': 'b1', 'position': 0, 'source_order': 1, 'kind': 'paragraph',
                 'text': 'Los requisitos de residencia están disponibles para consulta.', 'data': {}, 'flags': [], 'section_path': []}],
        events=[{'reason': 'personal_experience', 'text': 'Experiencia descartada.', 'source_order': 2, 'requires_review': True}],
    )
    with Database(path) as database:
        database.store_article(article, raw_html='<html>respaldo</html>')
    manager = FakeJobs()
    app = create_app(path, job_manager=manager, test_config={'TESTING': True})
    return app, app.test_client(), manager


def headers(app, **extra):
    return {'X-Panel-Token': app.config['PANEL_TOKEN'], **extra}


def test_page_and_date_search_read_existing_corpus(panel):
    app, client, _ = panel
    page = client.get('/')
    assert page.status_code == 200
    assert app.config['PANEL_TOKEN'] in page.get_data(as_text=True)
    assert "script-src 'self'" in page.headers['Content-Security-Policy']
    assert client.get('/api/summary').json['articles'] == 1
    found = client.get('/api/articles', query_string={'q': 'guia', 'date_field': 'modified_at', 'date_from': '2026-10-10', 'date_to': '2026-10-10'})
    assert found.status_code == 200
    assert found.json['total'] == 1
    assert found.json['items'][0]['modified_at'] == '2026-10-10T12:00:00+00:00'
    assert client.get('/api/articles?date_field=published_at&date_from=2026-10-01').json['total'] == 0


def test_detail_is_for_review_but_export_excludes_quarantine(panel):
    _, client, _ = panel
    detail = client.get('/api/articles/1').json
    assert detail['original_text'] == 'Una experiencia personal descartada.'
    assert detail['events'][0]['original_text'] == 'Experiencia descartada.'
    exported = client.get('/api/articles/1/export')
    assert exported.status_code == 200
    assert 'attachment' in exported.headers['Content-Disposition']
    text = exported.get_data(as_text=True)
    assert 'Autor Original' not in text
    assert 'Experiencia descartada' not in text
    assert json.loads(text)['content']['blocks'][0]['text'].startswith('Los requisitos')
    assert client.get('/api/articles/999').status_code == 404


def test_selection_requires_current_local_panel_token(panel):
    app, client, _ = panel
    body = {'article_ids': [1], 'selected': True}
    assert client.post('/api/selection', json=body).status_code == 403
    assert client.post('/api/selection', json=body, headers=headers(app, Origin='https://unrelated.example')).status_code == 403
    assert client.post('/api/selection', json=body, headers=headers(app, Origin='http://localhost')).status_code == 200
    assert client.get('/api/articles?selected_only=true').json['total'] == 1
    assert client.get('/api/summary').json['selected_count'] == 1
    assert client.post('/api/selection', json={'article_ids': [1], 'selected': False}, headers=headers(app)).status_code == 200
    assert client.get('/api/articles?selected_only=true').json['total'] == 0


@pytest.mark.parametrize('query', ['page=no', 'page=0', 'date_from=2026-99-01', 'date_field=garbage', 'selected_only=maybe', 'sort=DROP TABLE', 'review=unknown'])
def test_invalid_filters_are_actionable_errors(panel, query):
    _, client, _ = panel
    response = client.get('/api/articles?' + query)
    assert response.status_code == 400
    assert response.json['error']


def test_jobs_accepted_pollable_and_busy_conflict(panel):
    app, client, manager = panel
    response = client.post('/api/jobs', json={'action': 'discover'}, headers=headers(app))
    assert response.status_code == 202
    assert client.get('/api/jobs/job-1').json['status'] == 'queued'
    assert len(client.get('/api/jobs').json['items']) == 1
    manager.busy = True
    assert client.post('/api/jobs', json={'action': 'discover'}, headers=headers(app)).status_code == 409
    assert client.get('/api/jobs/missing').status_code == 404
    assert client.post('/api/jobs', json=[], headers=headers(app)).status_code == 400


def test_unknown_host_is_rejected(panel):
    _, client, _ = panel
    assert client.get('/api/summary', headers={'Host': 'unrelated.example'}).status_code == 400
