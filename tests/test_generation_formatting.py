"""Automatic formatting remains separate, current and reusable without a model."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from test_deployment_trial import saved_trial, Session, send
from src.generation import formatting
from src.generation.trial import get_trial
from src.generation.repository import GenerationRepository
from src.generation.content_edits import effective_content
from src.deployment.content import validate_semantic_html
from src.deployment.management import delete_adaptation, initialize_management, save_adaptation_content
from scripts.run_generation_trial import export_trial
from scripts.run_automatic_trial import _saved_results
from src.generation.assessment import _SCHEMA as ASSESSMENT_SCHEMA


ORIGINAL = '<h2>Jubilación</h2><p>Texto conservado.</p>\n[CTA_INMIGRACION_ES]\n<h2>Impuestos</h2><p>Texto fiscal.</p>\n[CTA_IMPUESTOS_ES]'


def source_html(path, text=ORIGINAL):
    with sqlite3.connect(path) as db:
        db.execute('UPDATE generation_trials SET html=?,response_json=? WHERE id=1',
                   (text, json.dumps({'output': text})))


def format_rows(path):
    with sqlite3.connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='generation_html_formats'").fetchone():
            return []
        return db.execute('SELECT source_sha256,html,report_json FROM generation_html_formats').fetchall()


def test_root_cta_repair_is_effective_and_original_response_usage_unchanged(saved_trial, tmp_path, monkeypatch):
    path, _ = saved_trial
    source_html(path)
    before = get_trial(path, 1)
    monkeypatch.setattr(requests.Session, 'request', lambda *args, **kwargs: pytest.fail('formatting cannot call a provider'))
    result = formatting.ensure_trial_format(path, 1)
    assert result['original_html'] == ORIGINAL and result['html'] != ORIGINAL
    assert result['response'] == before['response'] and result['usage'] == before['usage']
    assert result['html_source'] == 'normalized' and result['edit_version'] == 0
    assert result['normalization']['status'] == 'normalized'
    assert result['normalization']['operations'] and result['normalization']['original_validation']['valid'] is False
    assert result['content_revision'] != before['content_revision']
    assert validate_semantic_html(result['html'])['valid']
    assert formatting.ensure_trial_format(path, 1)['html'] == result['html']
    assert len(format_rows(path)) == 1
    assert 'html' not in json.loads(format_rows(path)[0][2])
    repository = GenerationRepository(path)
    detail = repository.trial_detail(1)
    assert detail['html_source'] == 'normalized' and detail['html'] == result['html']
    history = repository.article_history(1)['trials'][0]
    assert history['html_source'] == 'normalized' and 'html' not in history
    exported = export_trial(result, title='Prueba de formato', output_dir=tmp_path/'export')
    assert Path(exported['artifacts']['body']).read_text(encoding='utf-8') == result['html']
    meta = json.loads(Path(exported['artifacts']['metadata']).read_text(encoding='utf-8'))
    assert meta['normalization']['status'] == 'normalized' and meta['html_source'] == 'normalized'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT html FROM generation_trials WHERE id=1').fetchone()[0] == ORIGINAL


def test_existing_valid_output_is_byte_exact_without_artifact(saved_trial):
    path, _ = saved_trial
    original = get_trial(path, 1)
    normalized = formatting.ensure_trial_format(path, 1)
    assert normalized['html'] == original['html'] and normalized['content_revision'] == original['content_revision']
    assert normalized['normalization']['status'] == 'unchanged' and format_rows(path) == []


def test_unsafe_output_is_preserved_and_never_becomes_effective_repair(saved_trial):
    path, _ = saved_trial
    text = '<script>alert(1)</script><p>Retained.</p>'
    source_html(path, text)
    result = formatting.ensure_trial_format(path, 1)
    assert result['html'] == text and result['html_source'] == 'model'
    assert result['normalization']['status'] == 'unresolved'
    assert not result['normalization']['validation']['valid']
    assert format_rows(path) == []


def test_manual_revision_overrides_normalization_and_is_not_auto_rewritten(saved_trial, monkeypatch):
    path, _ = saved_trial
    source_html(path)
    repaired = formatting.ensure_trial_format(path, 1)
    edit = save_adaptation_content(path, 1, html='<p>Edición manual.</p>', revision=repaired['content_revision'])
    monkeypatch.setattr(formatting, 'normalize_generated_html', lambda *_: pytest.fail('must not normalize an editor revision'))
    current = formatting.ensure_trial_format(path, 1)
    assert current['html'] == edit['html'] and current['html_source'] == 'editor'
    assert current['normalization']['status'] == 'editor'
    assert len(format_rows(path)) == 1


def test_concurrent_editor_wins_without_saving_stale_normalization(saved_trial, monkeypatch):
    path, _ = saved_trial
    source_html(path)
    revision = get_trial(path, 1)['content_revision']
    normalize = formatting.normalize_generated_html
    def edit_during_format(html):
        result = normalize(html)
        save_adaptation_content(path, 1, html='<p>Edición simultánea.</p>', revision=revision)
        return result
    monkeypatch.setattr(formatting, 'normalize_generated_html', edit_during_format)
    result = formatting.ensure_trial_format(path, 1)
    assert result['html'] == '<p>Edición simultánea.</p>' and result['html_source'] == 'editor'
    assert format_rows(path) == []


def test_concurrent_delete_cannot_restore_content(saved_trial, monkeypatch):
    path, _ = saved_trial
    source_html(path)
    normalize = formatting.normalize_generated_html
    def delete_during_format(html):
        result = normalize(html)
        delete_adaptation(path, 1)
        return result
    monkeypatch.setattr(formatting, 'normalize_generated_html', delete_during_format)
    with pytest.raises(KeyError):
        formatting.ensure_trial_format(path, 1)
    assert format_rows(path) == []


def test_original_hash_anchor_prevents_using_an_old_format_for_different_output(saved_trial):
    path, _ = saved_trial
    source_html(path)
    formatting.ensure_trial_format(path, 1)
    replacement = '<h2>Otro resultado</h2><p>Distinto.</p>'
    source_html(path, replacement)
    assert get_trial(path, 1)['html'] == replacement
    assert get_trial(path, 1)['html_source'] == 'model'


def test_corrupt_artifact_not_used(saved_trial):
    path, _ = saved_trial
    source_html(path)
    formatting.ensure_trial_format(path, 1)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE generation_html_formats SET html='<p>tampered</p>'")
    assert get_trial(path, 1)['html_source'] == 'model'
    assert get_trial(path, 1)['html'] == ORIGINAL


def test_deletion_purges_all_formatted_content(saved_trial):
    path, _ = saved_trial
    source_html(path)
    formatting.ensure_trial_format(path, 1)
    delete_adaptation(path, 1)
    assert format_rows(path) == []
    assert GenerationRepository(path).article_history(1)['trials'] == []


def test_wordpress_uses_normalized_body_without_changing_model_output(saved_trial):
    path, _ = saved_trial
    source_html(path)
    normalized = formatting.ensure_trial_format(path, 1)
    remote = Session()
    result = send(saved_trial, remote, allow_pending_ctas=True)
    assert result['status'] == 'draft'
    payload = next(call[2]['json'] for call in remote.calls if call[0] == 'POST')
    assert payload['content'] == normalized['html']
    assert get_trial(path, 1)['original_html'] == ORIGINAL


def test_inflight_deployment_prevents_format_change(saved_trial):
    path, _ = saved_trial
    # Reserve a draft with valid original, then simulate a newly captured invalid
    # format: the normalizer must not alter effective content during that send.
    send(saved_trial, Session(exception=requests.Timeout()), allow_pending_ctas=True)
    source_html(path)
    with pytest.raises(ValueError, match='envío en curso'):
        formatting.ensure_trial_format(path, 1)
    assert format_rows(path) == []


def saved_assessment_pair(path):
    request = {'max_output_tokens': 12000, 'max_tool_calls': 3}
    with sqlite3.connect(path) as db:
        db.execute(ASSESSMENT_SCHEMA)
        db.execute("""INSERT INTO generation_assessments
            (id,run_key,article_id,configuration_id,context_fingerprint,context_json,model,reasoning_effort,
             service_tier,status,request_json,created_at)
            VALUES (1,'resume.assessment',1,3,'fingerprint','{}','gpt-test','','default','completed',?,'2026-11-06')""", (json.dumps(request),))
        db.execute("UPDATE generation_trials SET run_key='resume.generation' WHERE id=1")


def test_saved_results_reads_exact_pair_without_provider(saved_trial, monkeypatch):
    path, _ = saved_trial
    saved_assessment_pair(path)
    monkeypatch.setattr(requests.Session, 'request', lambda *args, **kwargs: pytest.fail('resume cannot invoke OpenAI'))
    assessment, trial = _saved_results(path, 1, 3, 'gpt-test', 'resume', '', 'default')
    assert assessment['id'] == trial['id'] == 1


@pytest.mark.parametrize('changed', ['model', 'reasoning_effort', 'service_tier', 'status', 'request_json', 'article_id'])
def test_saved_pair_rejects_parameter_mismatch(saved_trial, changed):
    path, _ = saved_trial
    saved_assessment_pair(path)
    values = {'model':'gpt-other','reasoning_effort':'high','service_tier':'flex','status':'unknown',
              'request_json':'{}','article_id':2}
    with sqlite3.connect(path) as db:
        db.execute(f'UPDATE generation_assessments SET {changed}=?', (values[changed],))
    with pytest.raises(ValueError, match='evaluación guardada'):
        _saved_results(path, 1, 3, 'gpt-test', 'resume', '', 'default')


def test_saved_pair_requires_exact_run_key_and_undeleted_trial(saved_trial):
    path, _ = saved_trial
    saved_assessment_pair(path)
    with pytest.raises(ValueError, match='Falta un resultado'):
        _saved_results(path, 1, 3, 'gpt-test', 'another', '', 'default')
    delete_adaptation(path, 1)
    with pytest.raises(ValueError, match='eliminado'):
        _saved_results(path, 1, 3, 'gpt-test', 'resume', '', 'default')
