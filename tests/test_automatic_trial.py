"""Stage boundaries of the API-only adaptation orchestrator; no network calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scripts import run_automatic_trial as pipeline
from src.deployment.repository import DeploymentRepository
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.applicability import review_status
from src.generation.html_normalization import normalize_generated_html


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    path = tmp_path / "articles.db"
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with Database(path) as db:
        db.store_article(ExtractionResult(url="https://movingtospain.com/renewal/",
            canonical_url="https://movingtospain.com/renewal/", title="Digital Nomad Renewal",
            original_text="Base article", blocks=[{"key": "b1", "position": 0, "source_order": 1,
            "kind": "paragraph", "text": "Base article", "section_path": [], "data": {}, "flags": []}]),
            raw_html="<p>Base article</p>")
    DeploymentRepository(path, env_path=env)
    status = review_status(path, 1, 3)
    status.update(allowed=True, state="current", current_review={"id": 88, "source": "model", "assessment_id": 7})
    context = status["context"]
    taxonomy = {"target_id": 3, "catalog_revision": "catalog-v1", "policy_revision": "policy-v1",
                "excluded_default_category_id": 1, "candidates": [
                    {"id": 20, "name": "Inmigración", "slug": "inmigracion", "description": "", "parent": 0},
                    {"id": 26, "name": "Trabajo remoto", "slug": "remoto", "description": "", "parent": 0}]}
    assessment = {"id": 7, "status": "completed", "context_fingerprint": status["context_fingerprint"],
        "decision": {"decision": "adapt", "reason": "El tema central es aplicable según la evaluación de la API."},
        "suggested_title": "Renovación del permiso de nómada digital para colombianos",
        "usage": {"input_tokens": 100, "output_tokens": 200},
        "category_selection": {**deepcopy(taxonomy), "selected_category_ids": [26], "source": "model"}}
    trial = {"id": 12, "run_key": "test.generation", "model": "gpt-6-luna", "status": "completed",
        "html": "<p>Texto original devuelto por la API.</p><h2>Preparar la renovación</h2><p>Conserve sus documentos.</p>",
        "request": {"reasoning": {"effort": "high"}, "service_tier": "flex", "max_output_tokens": 16000},
        "snapshot": {"applicability_review": {"source": "model", "assessment_id": 7}},
        **{key: context[key] for key in ("article_id", "source_version_id", "configuration_id",
            "configuration_version_id", "prompt_version_id", "destination_id")}}
    cover = {"selection_id": 13, "status": "ready", "wp_media_id": 4321, "source_version_id": context["source_version_id"],
             "target_id": 3, "wp_media_url": "https://colombianosespana.com/wp-content/uploads/automatic.jpg"}
    calls = {key: [] for key in ("assessment", "generation", "cover", "deployment")}
    state = {"path": path, "env": env, "output": tmp_path / "output", "status": status,
             "assessment": assessment, "trial": trial, "cover": cover, "calls": calls, "taxonomy": taxonomy}
    monkeypatch.setattr(pipeline, "category_context", lambda *args, **kwargs: deepcopy(state["taxonomy"]))
    monkeypatch.setattr(pipeline, "get_cached_category_context", lambda *args, **kwargs: deepcopy(state["taxonomy"]))
    def validate_categories(db, target_id, selected, **kwargs):
        if not selected or set(selected) - {item["id"] for item in state["taxonomy"]["candidates"]}:
            raise ValueError("Categorías ajenas al catálogo permitido.")
        return {"category_ids": sorted(selected), "categories": [item for item in state["taxonomy"]["candidates"]
                if item["id"] in selected], "catalog_revision": state["taxonomy"]["catalog_revision"]}
    monkeypatch.setattr(pipeline, "validate_category_selection", validate_categories)
    monkeypatch.setattr(pipeline, "review_status", lambda *args: deepcopy(state["status"]))
    def assess(*args, **kwargs):
        calls["assessment"].append((args, kwargs))
        return deepcopy(state["assessment"])
    def generate(*args, **kwargs):
        calls["generation"].append((args, kwargs))
        if state.get("change_during_generation"):
            state["status"]["context_fingerprint"] = "new-context"
        return deepcopy(state["trial"])
    def choose_cover(*args, **kwargs):
        calls["cover"].append((args, kwargs))
        return deepcopy(state["cover"])
    def format_saved_trial(*args):
        # Stage-boundary tests use a fake persisted trial. Persistence and its
        # optimistic checks are covered separately in test_generation_formatting.
        value = deepcopy(state["trial"])
        normalized = normalize_generated_html(value["html"])
        value["html"] = normalized["html"]
        value["normalization"] = {key: normalized[key] for key in ("changed", "valid", "operations", "policy")}
        return value
    def send(*args, **kwargs):
        calls["deployment"].append((args, kwargs))
        return {"id": 19, "status": "draft", "result": {"post_id": 5000, "post_status": "draft"}, "warnings": []}
    monkeypatch.setattr(pipeline, "run_assessment", assess)
    monkeypatch.setattr(pipeline, "run_trial", generate)
    monkeypatch.setattr(pipeline, "auto_import_cover", choose_cover)
    monkeypatch.setattr(pipeline, "send_trial_draft", send)
    monkeypatch.setattr(pipeline, "ensure_trial_format", format_saved_trial)
    return state


def execute(state, **kwargs):
    return pipeline.run_automatic_trial(state["path"], 1, 3, 3, "gpt-6-luna", "test",
        env_path=state["env"], output_dir=state["output"], **kwargs)


@pytest.mark.parametrize("decision", ["omit", "needs_verification"])
def test_api_omission_or_verification_stops_all_later_stages(workflow, decision):
    workflow["assessment"]["decision"]["decision"] = decision
    result = execute(workflow, send_draft=True)
    assert result["status"] == "stopped" and result["stage"] == "assessment"
    assert result["decision"] == decision
    assert not any(workflow["calls"][key] for key in ("generation", "cover", "deployment"))


@pytest.mark.parametrize("status", ["running", "unknown", "failed", "incomplete"])
def test_noncompleted_assessment_cannot_spend_on_drafting(workflow, status):
    workflow["assessment"]["status"] = status
    result = execute(workflow, send_draft=True)
    assert result["stage"] == "assessment" and not workflow["calls"]["generation"]


@pytest.mark.parametrize("review", [None, {"source": "manual", "assessment_id": 7},
                                     {"source": "model", "assessment_id": 8}])
def test_assessment_must_own_current_model_review(workflow, review):
    workflow["status"]["current_review"] = review
    result = execute(workflow)
    assert result["status"] == "stopped" and not workflow["calls"]["generation"]


def test_stale_cached_assessment_is_not_used_for_new_context(workflow):
    workflow["assessment"]["context_fingerprint"] = "old-context"
    result = execute(workflow)
    assert result["status"] == "stopped" and not workflow["calls"]["generation"]


def test_invalid_html_is_not_rewritten_or_given_an_image(workflow):
    workflow["trial"]["html"] = '<script>unsafe()</script><p>Texto de API</p>'
    result = execute(workflow, send_draft=True)
    assert result["stage"] == "generation" and not result["semantic_validation"]["valid"]
    assert not workflow["calls"]["cover"] and not workflow["calls"]["deployment"]
    assert workflow["trial"]["html"] == '<script>unsafe()</script><p>Texto de API</p>'
    assert "<script>" in result['reason']


def test_root_ctas_are_normalized_before_cover_and_deployment(workflow):
    original = '<h2>Jubilación</h2><p>Texto de la API.</p>\n[CTA_INMIGRACION_ES]\n<h2>Impuestos</h2><p>Texto fiscal.</p>\n[CTA_IMPUESTOS_ES]'
    workflow['trial']['html'] = original
    result = execute(workflow, send_draft=True, allow_pending_ctas=True)
    assert result['status'] == 'draft' and result['semantic_validation']['valid']
    assert result['normalization']['changed']
    exported = Path(result['artifacts']['body']).read_text(encoding='utf-8')
    paragraphs = [p.get_text().strip() for p in BeautifulSoup(exported, 'html.parser').find_all('p')]
    assert '[CTA_INMIGRACION_ES]' in paragraphs
    assert '[CTA_IMPUESTOS_ES]' in paragraphs
    assert workflow['trial']['html'] == original
    assert len(workflow['calls']['assessment']) == len(workflow['calls']['generation']) == 1


def test_reuse_only_never_invokes_provider_wrappers(workflow, monkeypatch):
    monkeypatch.setattr(pipeline, '_saved_results', lambda *args: (deepcopy(workflow['assessment']), deepcopy(workflow['trial'])))
    result = execute(workflow, reuse_only=True, import_cover=False)
    assert result['status'] == 'ready'
    assert not workflow['calls']['assessment'] and not workflow['calls']['generation']


def test_reuse_only_missing_saved_pair_has_no_paid_fallback(workflow):
    with pytest.raises(ValueError, match='guardadas'):
        execute(workflow, reuse_only=True, send_draft=True)
    assert not any(workflow['calls'].values())


def test_incomplete_generation_does_not_import_a_cover(workflow):
    workflow["trial"]["status"] = "incomplete"
    result = execute(workflow, send_draft=True)
    assert result["stage"] == "generation" and not workflow["calls"]["cover"]


def test_uncertain_cover_prevents_export_and_draft(workflow):
    workflow["cover"]["status"] = "unknown"
    result = execute(workflow, send_draft=True)
    assert result["stage"] == "image" and result["status"] == "stopped"
    assert not workflow["calls"]["deployment"] and not workflow["output"].exists()


def test_old_cover_cannot_attach_to_new_source_version(workflow):
    workflow["cover"]["source_version_id"] = 900
    result = execute(workflow, send_draft=True)
    assert result["status"] == "stopped" and not workflow["calls"]["deployment"]


def test_source_change_after_generation_stops_before_image(workflow):
    workflow["change_during_generation"] = True
    result = execute(workflow, send_draft=True)
    assert result["stage"] == "generation" and not workflow["calls"]["cover"]


def test_ready_result_preserves_api_body_title_and_automatic_image(workflow):
    result = execute(workflow, send_draft=True, category_ids=[26, 20], allow_pending_ctas=True)
    assert result["status"] == "draft" and result["wordpress"]["post_id"] == 5000
    assert Path(result["artifacts"]["body"]).read_text(encoding="utf-8") == workflow["trial"]["html"]
    metadata = json.loads(Path(result["artifacts"]["metadata"]).read_text(encoding="utf-8"))
    assert metadata["title"] == workflow["assessment"]["suggested_title"]
    assert metadata["featured_media"] == 4321
    args, kwargs = workflow["calls"]["assessment"][0]
    assert args[-2:] == ("gpt-6-luna", "test.assessment")
    assert kwargs["reasoning_effort"] == "high" and kwargs["max_tool_calls"] == 3
    args, kwargs = workflow["calls"]["generation"][0]
    assert args[-2:] == ("gpt-6-luna", "test.generation")
    assert kwargs["reasoning_effort"] == "high" and kwargs["max_output_tokens"] == 16000
    args, kwargs = workflow["calls"]["cover"][0]
    assert args == (workflow["path"], 3, 1) and set(kwargs) == {"env_path"}
    args, kwargs = workflow["calls"]["deployment"][0]
    assert kwargs["title"] == workflow["assessment"]["suggested_title"]
    assert kwargs["category_ids"] == [20, 26] and kwargs["allow_pending_ctas"] is True
    assert kwargs["featured_media"] == 4321


def test_export_only_does_not_send_wordpress_entry(workflow):
    result = execute(workflow)
    assert result["status"] == "ready" and not workflow["calls"]["deployment"]


def test_local_adaptation_skips_all_image_and_wordpress_calls(workflow):
    result = execute(workflow, import_cover=False)
    assert result['status'] == 'ready' and result['stage'] == 'export'
    assert not workflow['calls']['cover'] and not workflow['calls']['deployment']
    assert Path(result['artifacts']['body']).read_text(encoding='utf-8') == workflow['trial']['html']
    metadata = json.loads(Path(result['artifacts']['metadata']).read_text(encoding='utf-8'))
    assert metadata['featured_media'] is None and metadata['image_url'] is None


def test_model_selects_dynamic_categories_in_existing_assessment_call(workflow):
    result = execute(workflow, send_draft=True)
    assert result["status"] == "draft" and result["category_ids"] == [26]
    assert workflow["calls"]["assessment"][0][1]["category_context"] == workflow["taxonomy"]
    assert len(workflow["calls"]["assessment"]) == len(workflow["calls"]["generation"]) == 1
    assert workflow["calls"]["deployment"][0][1]["category_ids"] == [26]


def test_local_generation_uses_cached_categories_without_remote_fetch(workflow, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Local generation must not fetch the remote catalog")
    monkeypatch.setattr(pipeline, "category_context", forbidden)
    result = execute(workflow, import_cover=False)
    assert result["status"] == "ready" and result["category_ids"] == [26]
    assert workflow["calls"]["assessment"][0][1]["category_context"] == workflow["taxonomy"]


def test_local_generation_without_configured_categories_still_works(workflow, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("Configure las categorías")
    monkeypatch.setattr(pipeline, "get_cached_category_context", unavailable)
    workflow["assessment"].pop("category_selection")
    result = execute(workflow, import_cover=False)
    assert result["status"] == "ready" and result["category_ids"] == []
    assert workflow["calls"]["assessment"][0][1]["category_context"] is None


def test_draft_requires_live_category_policy_before_any_paid_stage(workflow, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("Configure las categorías")
    monkeypatch.setattr(pipeline, "category_context", unavailable)
    with pytest.raises(ValueError, match="Configure"):
        execute(workflow, send_draft=True)
    assert not any(workflow["calls"].values())


@pytest.mark.parametrize("legacy", [True, False])
def test_missing_category_choice_preserves_local_generation_without_image(workflow, legacy):
    if legacy:
        workflow["assessment"].pop("category_selection")
    else:
        workflow["assessment"]["category_selection"]["selected_category_ids"] = []
    result = execute(workflow, send_draft=True)
    assert result["stage"] == "categories" and result["category_selection_pending"]
    assert result["trial_id"] == 12 and len(workflow["calls"]["generation"]) == 1
    assert len(workflow["calls"]["assessment"]) == 1
    assert not workflow["calls"]["cover"] and not workflow["calls"]["deployment"]


def test_legacy_reuse_does_not_regenerate_to_obtain_categories(workflow, monkeypatch):
    workflow["assessment"].pop("category_selection")
    monkeypatch.setattr(pipeline, "_saved_results", lambda *args: (deepcopy(workflow["assessment"]), deepcopy(workflow["trial"])))
    result = execute(workflow, reuse_only=True, send_draft=True)
    assert result["stage"] == "categories" and result["category_selection_pending"]
    assert not any(workflow["calls"].values())


def test_legacy_reuse_with_explicit_valid_categories_never_calls_provider(workflow, monkeypatch):
    workflow["assessment"].pop("category_selection")
    monkeypatch.setattr(pipeline, "_saved_results", lambda *args: (deepcopy(workflow["assessment"]), deepcopy(workflow["trial"])))
    result = execute(workflow, reuse_only=True, send_draft=True, category_ids=[20])
    assert result["status"] == "draft" and result["category_ids"] == [20]
    assert not workflow["calls"]["assessment"] and not workflow["calls"]["generation"]


def test_changed_category_blocks_image_upload_and_draft(workflow, monkeypatch):
    def stale(*args, **kwargs):
        raise ValueError("Cambió la categoría seleccionada")
    monkeypatch.setattr(pipeline, "validate_category_selection", stale)
    result = execute(workflow, send_draft=True)
    assert result["stage"] == "categories" and result["category_selection_pending"]
    assert not workflow["calls"]["cover"] and not workflow["calls"]["deployment"]


def test_local_provider_default_does_not_require_explicit_reasoning_effort(workflow):
    workflow['trial']['request'].pop('reasoning')
    result = execute(workflow, import_cover=False, reasoning_effort='')
    assert result['status'] == 'ready'
    assert workflow['calls']['assessment'][0][1]['reasoning_effort'] == ''
    assert workflow['calls']['generation'][0][1]['reasoning_effort'] == ''
    assert not workflow['calls']['cover'] and not workflow['calls']['deployment']


def test_draft_cannot_bypass_automatic_cover(workflow):
    with pytest.raises(ValueError, match='portada automática'):
        execute(workflow, import_cover=False, send_draft=True)
    assert not any(workflow['calls'].values())


def test_replaying_orchestrator_keeps_each_durable_stage_key(workflow):
    execute(workflow, send_draft=True)
    execute(workflow, send_draft=True)
    assert {call[0][-1] for call in workflow["calls"]["assessment"]} == {"test.assessment"}
    assert {call[0][-1] for call in workflow["calls"]["generation"]} == {"test.generation"}
    assert {call[0][-1] for call in workflow["calls"]["deployment"]} == {12}


@pytest.mark.parametrize("change", ["model", "effort", "tier", "max_output"])
def test_cached_generation_parameters_must_match_requested_trial(workflow, change):
    if change == "model":
        workflow["trial"]["model"] = "gpt-5-nano"
    elif change == "effort":
        workflow["trial"]["request"]["reasoning"]["effort"] = "low"
    elif change == "tier":
        workflow["trial"]["request"]["service_tier"] = "default"
    else:
        workflow["trial"]["request"]["max_output_tokens"] = 8000
    result = execute(workflow, send_draft=True)
    assert result["status"] == "stopped" and not workflow["calls"]["cover"]


def test_wrong_target_fails_before_any_api_call(workflow):
    with pytest.raises(ValueError, match="mismo público"):
        pipeline.run_automatic_trial(workflow["path"], 1, 3, 1, "gpt-6-luna", "test")
    assert not any(workflow["calls"].values())


def test_cached_manually_reviewed_generation_cannot_pass_as_automatic(workflow):
    workflow["trial"]["snapshot"]["applicability_review"] = {"source": "manual", "assessment_id": None}
    result = execute(workflow, send_draft=True)
    assert result["status"] == "stopped" and not workflow["calls"]["cover"]


def test_disabled_destination_fails_before_any_api_call(workflow):
    workflow["status"]["context"]["destination_enabled"] = False
    with pytest.raises(ValueError):
        execute(workflow)
    assert not any(workflow["calls"].values())


def test_cli_exposes_high_automatic_flow_without_manual_title_or_image(workflow, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "run_automatic_trial", lambda *args, **kwargs: {"status": "ready"})
    assert pipeline.main(["--article-id", "1", "--configuration-id", "3", "--target-id", "3",
        "--model", "gpt-6-luna", "--run-key", "test", "--reasoning-effort", "high",
        "--send-draft", "--category-id", "20", "--category-id", "26", "--allow-pending-ctas"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
