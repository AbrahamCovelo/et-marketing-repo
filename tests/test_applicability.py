"""Editorial applicability decisions gate every new paid drafting request."""
import json
import sqlite3

import pytest
import requests

from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.applicability import (
    ApplicabilityRequiredError, build_assessment_prompt, parse_assessment_candidate,
    initialize_applicability, require_review, review_status, save_model_review, save_review,
)
from src.generation import applicability as applicability_module
from src.generation.presets import load_editorial_prompts
from src.generation.repository import GenerationRepository
from src.generation.trial import _SCHEMA, list_trials, run_trial
from test_generation_trial import Session, MODEL


def source(text='Renting a home in Madrid; a US-only section discusses an exclusive US allowance.'):
    return ExtractionResult(
        url='https://movingtospain.com/example/', canonical_url='https://movingtospain.com/example/',
        title='Renting in Madrid', original_text='WITHHELD AUTHOR',
        blocks=[{'key': 'p1', 'position': 0, 'source_order': 1, 'kind': 'paragraph', 'text': text,
                 'section_path': [], 'data': {}, 'flags': []}],
    )


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    path = tmp_path / 'source.db'
    env = tmp_path / '.env'
    env.write_text('OPENAI_API_KEY=sk-fixture-only', encoding='utf-8')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: pytest.fail('Real HTTP forbidden'))
    with Database(path) as db:
        db.store_article(source(), raw_html='<p>Raw author omitted.</p>')
    load_editorial_prompts(path)
    return path, env


def payload(decision='adapt'):
    data = {
        'decision': decision, 'central_question': '¿Cómo alquilar una vivienda en Madrid?',
        'reason': 'El núcleo trata de alquiler y sirve a lectores colombianos.', 'reviewer': 'Editor de prueba',
        'excluded_sections': [{'section': 'US-only allowance', 'reason': 'El apartado no se aplica al público colombiano.'}],
        'evidence': [], 'checks': {'central_applicable': True, 'whole_article_exclusive': False,
                                  'alternative_applicable': None, 'essential_information_verified': True},
    }
    if decision == 'omit':
        data.update(central_question='¿Cómo acceder a un beneficio exclusivo estadounidense?',
                    reason='La condición central es exclusiva y no existe alternativa aplicable según la revisión.',
                    evidence=[{'url': 'https://example.gov/official-benefit', 'summary': 'Fuente ficticia del test: beneficio exclusivo sin alternativa.', 'checked_at': '2026-11-04'}],
                    checks={'central_applicable': False, 'whole_article_exclusive': True,
                            'alternative_applicable': False, 'essential_information_verified': True})
    if decision == 'needs_verification':
        data.update(reason='Falta comprobar si existe una alternativa para este público.',
                    checks={'central_applicable': None, 'whole_article_exclusive': True,
                            'alternative_applicable': None, 'essential_information_verified': False})
    return data


def execute(corpus, session):
    path, env = corpus
    return run_trial(path, 1, 3, MODEL, 'screening-test-1', env_path=env, session=session)


@pytest.mark.parametrize('decision', [None, 'omit', 'needs_verification'])
def test_missing_or_negative_decision_blocks_before_credentials_reservation_and_network(corpus, monkeypatch, decision):
    path, _ = corpus
    if decision:
        save_review(path, 1, 3, payload(decision))
    import src.generation.trial as trial_module
    monkeypatch.setattr(trial_module, '_file_credentials', lambda *a: pytest.fail('Credentials were read before approval'))
    session = Session()
    with pytest.raises(ApplicabilityRequiredError):
        execute(corpus, session)
    assert session.calls == [] and list_trials(path) == []


def test_mixed_article_retains_main_question_and_injects_exclusions_outside_html(corpus):
    path, _ = corpus
    review = save_review(path, 1, 3, payload())
    session = Session()
    trial = execute(corpus, session)
    instructions = session.calls[0][2]['json']['instructions']
    assert instructions.startswith('CONTROL EDITORIAL PREVIO')
    assert 'US-only allowance' in instructions
    assert 'WITHHELD AUTHOR' not in json.dumps(trial)
    assert trial['snapshot']['applicability_review']['id'] == review['id']
    assert trial['snapshot']['applicability_review']['decision'] == 'adapt'
    assert trial['html'] == '<p>Barcelona tiene transporte público.</p>'


def test_reviews_are_population_specific(corpus):
    path, _ = corpus
    save_review(path, 1, 3, payload())
    assert review_status(path, 1, 3)['allowed']
    assert review_status(path, 1, 1)['state'] == 'not_reviewed'
    with pytest.raises(ApplicabilityRequiredError):
        require_review(path, 1, 1)


@pytest.mark.parametrize('change', ['source', 'configuration', 'destination', 'prompt'])
def test_review_becomes_stale_when_its_pinned_inputs_change(corpus, change):
    path, _ = corpus
    save_review(path, 1, 3, payload())
    repository = GenerationRepository(path)
    context = repository.configuration_context(3)
    if change == 'source':
        with Database(path) as db:
            db.store_article(source('New source details.'), raw_html='<p>New source</p>')
    elif change == 'destination':
        destination = context['destination']
        fields = {key: destination[key] for key in ('name', 'site_url', 'locale', 'notes', 'enabled')}
        fields['notes'] = 'New editorial audience constraints'
        repository.save_destination(fields, destination['id'])
    else:
        configuration = context['configuration']
        fields = {key: configuration[key] for key in ('name', 'destination_id', 'prompt_version_id', 'provider', 'model', 'notes', 'enabled')}
        if change == 'prompt':
            prompt = context['prompt']
            next_prompt = repository.save_prompt({key: prompt[key] for key in ('name', 'description', 'system_prompt', 'template', 'status', 'archived')} | {'template': prompt['template'] + '\nNueva regla.'}, prompt['id'])
            fields['prompt_version_id'] = next_prompt['version_id']
        else:
            fields['model'] = 'gpt-other-model'
        repository.save_configuration(fields, configuration['id'])
    assert review_status(path, 1, 3)['state'] == 'stale'
    with pytest.raises(ApplicabilityRequiredError):
        execute(corpus, Session())


def test_newer_pending_review_revokes_prior_approval_without_erasing_history(corpus):
    path, _ = corpus
    first = save_review(path, 1, 3, payload())
    second = save_review(path, 1, 3, payload('needs_verification'))
    assert first['id'] != second['id']
    assert review_status(path, 1, 3)['current_review']['decision'] == 'needs_verification'
    with pytest.raises(ApplicabilityRequiredError):
        execute(corpus, Session())
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM generation_applicability_reviews').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE generation_applicability_reviews SET decision='adapt'")


def test_review_revoked_during_preparation_prevents_reservation(corpus, monkeypatch):
    path, _ = corpus
    save_review(path, 1, 3, payload())
    import src.generation.trial as trial_module

    def revoke_before_reservation(*args):
        save_review(path, 1, 3, payload('needs_verification'))
        return {'OPENAI_API_KEY': 'sk-fixture-only'}

    monkeypatch.setattr(trial_module, '_file_credentials', revoke_before_reservation)
    session = Session()
    with pytest.raises(ApplicabilityRequiredError):
        execute(corpus, session)
    assert session.calls == [] and list_trials(path) == []


def test_concurrent_cached_trial_still_respects_local_deletion(corpus, monkeypatch):
    path, _ = corpus
    save_review(path, 1, 3, payload())
    original = execute(corpus, Session())
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE adaptation_deletions(trial_id INTEGER,deleted_at TEXT)')
        db.execute("INSERT INTO adaptation_deletions VALUES (?, '2026-11-05')", (original['id'],))
    import src.generation.trial as trial_module
    # Simulate a request that checked the key just before another worker
    # finished its request and the editor then deleted that adaptation.
    monkeypatch.setattr(trial_module, '_read_rows', lambda *a, **kw: [])
    session = Session()
    with pytest.raises(ValueError, match='eliminada'):
        execute(corpus, session)
    assert session.calls == []


def test_omission_requires_confirmed_whole_exclusivity_and_no_alternative(corpus):
    path, _ = corpus
    for key, value in [('whole_article_exclusive', False), ('alternative_applicable', None), ('essential_information_verified', False)]:
        data = payload('omit')
        data['checks'][key] = value
        with pytest.raises(ValueError, match='omitir'):
            save_review(path, 1, 3, data)
    data = payload('omit')
    data['evidence'] = []
    with pytest.raises(ValueError, match='omitir'):
        save_review(path, 1, 3, data)


def test_equivalence_requires_evidence_and_uncertainty_cannot_be_adapted(corpus):
    path, _ = corpus
    data = payload()
    data['checks']['whole_article_exclusive'] = True
    data['checks']['alternative_applicable'] = True
    with pytest.raises(ValueError, match='fuentes'):
        save_review(path, 1, 3, data)
    data['evidence'] = payload('omit')['evidence']
    saved = save_review(path, 1, 3, data)
    assert saved['evidence_verification'] == 'reviewer_reported_not_automatically_verified'
    assert saved['source'] == 'manual'
    assert 'no ha navegado' in __import__('src.generation.applicability', fromlist=['review_instructions']).review_instructions(saved)
    data['checks']['essential_information_verified'] = False
    with pytest.raises(ValueError, match='verificarse'):
        save_review(path, 1, 3, data)


def test_source_url_is_not_fetched_and_candidate_never_approves(corpus):
    path, _ = corpus
    candidate = payload('omit')
    candidate.pop('reviewer')
    result = parse_assessment_candidate(json.dumps(candidate))
    assert result['requires_editorial_review'] and result['decision'] == 'omit'
    assert review_status(path, 1, 3)['state'] == 'not_reviewed'
    prompt = build_assessment_prompt(path, 1, 3)
    assert prompt['execution_enabled'] is False
    assert 'Renting a home in Madrid' in prompt['input']
    assert 'WITHHELD AUTHOR' not in prompt['input']


def test_old_completed_trial_reuses_without_new_review_or_paid_call(corpus):
    path, env = corpus
    context = GenerationRepository(path).configuration_context(3)
    with sqlite3.connect(path) as db:
        db.execute(_SCHEMA)
        db.execute("""INSERT INTO generation_trials
            (run_key,article_id,source_version_id,configuration_id,configuration_version_id,prompt_version_id,
             destination_id,model,status,request_json,snapshot_json,html,created_at)
            VALUES ('screening-test-1',1,1,3,?,?,3,?,'completed','{"service_tier":"flex"}','{}','<p>Saved</p>','2026-11-04')""",
            (context['configuration']['current_version_id'], context['prompt']['version_id'], MODEL))
    env.unlink()
    session = Session()
    assert execute(corpus, session)['html'] == '<p>Saved</p>'
    assert session.calls == []


def test_review_save_rejects_stale_ui_context_and_readers_do_not_create_tables(corpus):
    path, _ = corpus
    status = review_status(path, 1, 3)
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='generation_applicability_reviews'").fetchone()
    with pytest.raises(ApplicabilityRequiredError, match='cambiaron'):
        save_review(path, 1, 3, payload(), expected_context_fingerprint='outdated')
    saved = save_review(path, 1, 3, payload(), expected_context_fingerprint=status['context_fingerprint'])
    assert saved['context_fingerprint'] == status['context_fingerprint']


@pytest.mark.parametrize('mutation', [
    lambda data: data.update(html='<p>Do not save a generated article as a decision</p>'),
    lambda data: data.update(decision='skip_every_american_article'),
    lambda data: data['checks'].update(central_applicable=1),
    lambda data: data['evidence'].append({'url': 'https://user:secret@example.gov/page', 'summary': 'No', 'checked_at': '2026-11-04'}),
    lambda data: data['evidence'].append({'url': 'https://example.gov/page', 'summary': 'No', 'checked_at': '2999-01-01'}),
])
def test_malformed_review_rejected_before_save(corpus, mutation):
    data = payload()
    mutation(data)
    with pytest.raises(ValueError):
        save_review(corpus[0], 1, 3, data)


def assessment_fixture(path, decision='adapt', *, status='completed', article_id=1,
                       configuration_id=3, fingerprint=None):
    """A completed provider journal fixture, never a live provider response."""
    context = review_status(path, 1, 3)
    data = payload(decision)
    data.pop('reviewer')
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS generation_assessments (
            id INTEGER PRIMARY KEY, article_id INTEGER, configuration_id INTEGER,
            context_fingerprint TEXT, status TEXT, model TEXT, decision_json TEXT)""")
        cursor = db.execute("""INSERT INTO generation_assessments
            (article_id,configuration_id,context_fingerprint,status,model,decision_json)
            VALUES (?,?,?,?,?,?)""", (article_id, configuration_id,
                fingerprint or context['context_fingerprint'], status, 'gpt-6-luna', json.dumps(data)))
        return cursor.lastrowid, context['context_fingerprint'], data


def test_completed_model_assessment_authorizes_drafting_without_manual_review(corpus):
    path, _ = corpus
    assessment_id, fingerprint, data = assessment_fixture(path)
    review = save_model_review(path, 1, 3, assessment_id=assessment_id,
                               expected_context_fingerprint=fingerprint)
    assert review['source'] == 'model'
    assert review['assessment_id'] == assessment_id
    assert review['reviewer'] == 'OpenAI: gpt-6-luna'
    assert review['evidence_verification'] == 'model_reported_with_tools_not_independently_verified'
    assert review_status(path, 1, 3)['allowed']
    session = Session()
    trial = execute(corpus, session)
    assert trial['snapshot']['applicability_review']['assessment_id'] == assessment_id
    instructions = session.calls[0][2]['json']['instructions']
    assert 'evaluación automática de OpenAI' in instructions
    assert 'no constituyen una revisión humana' in instructions
    assert 'no ha navegado' not in instructions
    assert review['reason'] == data['reason']


@pytest.mark.parametrize('decision', ['omit', 'needs_verification'])
def test_model_omission_and_uncertainty_block_new_drafting(corpus, decision):
    path, _ = corpus
    assessment_id, fingerprint, _ = assessment_fixture(path, decision)
    save_model_review(path, 1, 3, assessment_id=assessment_id,
                      expected_context_fingerprint=fingerprint)
    session = Session()
    with pytest.raises(ApplicabilityRequiredError):
        execute(corpus, session)
    assert session.calls == [] and list_trials(path) == []


def test_model_decision_cannot_be_rewritten_or_attributed_to_a_human(corpus):
    path, _ = corpus
    assessment_id, fingerprint, data = assessment_fixture(path, 'omit')
    altered = payload()
    altered.pop('reviewer')
    with pytest.raises(ValueError, match='no coincide'):
        save_model_review(path, 1, 3, altered, assessment_id=assessment_id,
                          expected_context_fingerprint=fingerprint)
    with pytest.raises(ValueError, match='identidad'):
        save_model_review(path, 1, 3, data | {'reviewer': 'Human professional'},
                          assessment_id=assessment_id, expected_context_fingerprint=fingerprint)
    assert review_status(path, 1, 3)['state'] == 'not_reviewed'
    saved = save_model_review(path, 1, 3, data | {'reviewer': 'OpenAI: gpt-6-luna'},
                              assessment_id=assessment_id, expected_context_fingerprint=fingerprint)
    assert saved['decision'] == 'omit'


@pytest.mark.parametrize('change', [
    {'status': 'running'}, {'status': 'failed'}, {'status': 'unknown'},
    {'article_id': 999}, {'configuration_id': 1}, {'fingerprint': 'a' * 64},
])
def test_incomplete_or_mismatched_provider_journal_cannot_approve(corpus, change):
    path, _ = corpus
    assessment_id, fingerprint, _ = assessment_fixture(path, **change)
    with pytest.raises(ApplicabilityRequiredError):
        save_model_review(path, 1, 3, assessment_id=assessment_id,
                          expected_context_fingerprint=fingerprint)
    assert review_status(path, 1, 3)['state'] == 'not_reviewed'


def test_missing_or_invalid_provider_journal_cannot_approve(corpus):
    path, _ = corpus
    fingerprint = review_status(path, 1, 3)['context_fingerprint']
    with pytest.raises(ApplicabilityRequiredError):
        save_model_review(path, 1, 3, assessment_id=1, expected_context_fingerprint=fingerprint)
    assessment_id, _, _ = assessment_fixture(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE generation_assessments SET decision_json='{}' WHERE id=?", (assessment_id,))
    with pytest.raises(ApplicabilityRequiredError, match='decisión'):
        save_model_review(path, 1, 3, assessment_id=assessment_id,
                          expected_context_fingerprint=fingerprint)


def test_reusing_model_review_does_not_revoke_later_editorial_decision(corpus):
    path, _ = corpus
    assessment_id, fingerprint, _ = assessment_fixture(path)
    first = save_model_review(path, 1, 3, assessment_id=assessment_id,
                              expected_context_fingerprint=fingerprint)
    latest = save_review(path, 1, 3, payload('needs_verification'))
    reused = save_model_review(path, 1, 3, assessment_id=assessment_id,
                               expected_context_fingerprint=fingerprint)
    assert reused['id'] == first['id']
    status = review_status(path, 1, 3)
    assert status['current_review']['id'] == latest['id'] and not status['allowed']
    assert latest['assessment_id'] is None and latest['source'] == 'manual'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM generation_applicability_reviews').fetchone()[0] == 2


def test_model_approval_rejects_changes_during_provider_call(corpus):
    path, _ = corpus
    assessment_id, fingerprint, _ = assessment_fixture(path)
    with Database(path) as db:
        db.store_article(source('Changed while the provider was deciding.'), raw_html='<p>Changed</p>')
    with pytest.raises(ApplicabilityRequiredError, match='cambiaron'):
        save_model_review(path, 1, 3, assessment_id=assessment_id,
                          expected_context_fingerprint=fingerprint)
    assert review_status(path, 1, 3)['state'] == 'not_reviewed'


def test_manual_only_schema_migrates_without_altering_history_or_constraints(corpus):
    path, _ = corpus
    saved = save_review(path, 1, 3, payload())
    with sqlite3.connect(path) as db:
        # Restore the exact previous schema contract to exercise a real upgrade.
        db.execute('DROP TRIGGER applicability_reviews_immutable_update')
        db.execute('DROP TRIGGER applicability_reviews_immutable_delete')
        db.execute('DROP INDEX applicability_pair')
        db.execute('ALTER TABLE generation_applicability_reviews RENAME TO new_reviews_fixture')
        old_schema = applicability_module._SCHEMA.replace(
            "source TEXT NOT NULL CHECK(source IN ('manual','model')),\n    assessment_id INTEGER UNIQUE,",
            "source TEXT NOT NULL CHECK(source='manual'),")
        old_schema = old_schema.replace(
            "    CHECK((source='manual' AND assessment_id IS NULL)\n       OR (source='model' AND assessment_id IS NOT NULL)),\n", '')
        db.execute(old_schema)
        columns = [row[1] for row in db.execute('PRAGMA table_info(generation_applicability_reviews)')]
        names = ','.join(columns)
        db.execute(f'INSERT INTO generation_applicability_reviews ({names}) SELECT {names} FROM new_reviews_fixture')
        db.execute('DROP TABLE new_reviews_fixture')
        for verb in ('UPDATE', 'DELETE'):
            db.execute(f"""CREATE TRIGGER applicability_reviews_immutable_{verb.lower()}
                BEFORE {verb} ON generation_applicability_reviews BEGIN
                SELECT RAISE(ABORT, 'Applicability reviews are immutable'); END""")
        before = db.execute('SELECT * FROM generation_applicability_reviews').fetchone()
    # Read-only access works even before the upgrade and fills the new nullable metadata.
    assert review_status(path, 1, 3)['current_review']['assessment_id'] is None
    initialize_applicability(path)
    initialize_applicability(path)
    with sqlite3.connect(path) as db:
        assert db.execute(f'SELECT {names} FROM generation_applicability_reviews').fetchone() == before
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT assessment_id FROM generation_applicability_reviews').fetchone()[0] is None
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM generation_applicability_reviews')
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("UPDATE generation_applicability_reviews SET reason='Changed'")
    assessment_id, fingerprint, _ = assessment_fixture(path)
    model_review = save_model_review(path, 1, 3, assessment_id=assessment_id,
                                     expected_context_fingerprint=fingerprint)
    assert model_review['id'] > saved['id']
    assert review_status(path, 1, 3)['current_review']['source'] == 'model'
