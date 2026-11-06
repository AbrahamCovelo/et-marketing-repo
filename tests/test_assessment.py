"""Automatic model assessment: provenance, zero duplicate spend and no manual edits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation import applicability
from src.generation.assessment import get_assessment, run_assessment
from src.generation.presets import load_editorial_prompts


SECRET = "sk-test-assessment-never-persist-secret"
SOURCE_URL = "https://www.inclusion.gob.es/official-telework"
MODEL = "gpt-6-luna"


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path, env = tmp_path / "articles.db", tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=" + SECRET, encoding="utf-8")
    with Database(path) as db:
        source = ExtractionResult(url="https://movingtospain.com/dnv/", canonical_url="https://movingtospain.com/dnv/",
            title="Digital nomad residence renewal", original_text="WITHHELD BYLINE",
            blocks=[{"key": "b1", "position": 0, "source_order": 1, "kind": "paragraph",
                     "text": "Renewing a digital nomad residence requires maintaining the conditions.",
                     "section_path": [], "data": {}, "flags": []}])
        db.store_article(source, raw_html="<main>WITHHELD RAW HTML</main>")
    load_editorial_prompts(path)
    manual = review_payload()
    manual.update(reviewer="MANUAL_REVIEWER_MARKER", reason="MANUAL_REASON_MUST_NOT_REACH_API",
                  excluded_sections=[{"section": "MANUAL_SECTION_MARKER", "reason": "MANUAL_EXCLUSION_MARKER"}],
                  evidence=[{"url": "https://boe.es/MANUAL_EVIDENCE_MARKER", "summary": "MANUAL_FACT_MARKER",
                             "checked_at": today()}])
    applicability.save_review(path, 1, 3, manual)
    return path, env


def today():
    return datetime.now(timezone.utc).date().isoformat()


def review_payload(decision="adapt"):
    return {"decision": decision, "reason": "El alcance general es aplicable según la fuente oficial consultada.",
            "central_question": "¿Cómo mantener los requisitos de residencia?", "excluded_sections": [],
            "evidence": [{"url": SOURCE_URL, "summary": "Mantener las condiciones de la autorización.", "checked_at": today()}],
            "checks": {"central_applicable": decision == "adapt", "whole_article_exclusive": decision == "omit",
                       "alternative_applicable": False if decision == "omit" else None,
                       "essential_information_verified": decision != "needs_verification"}}


def body(decision="adapt", **overrides):
    return {"id": "resp_assessment", "model": MODEL, "status": "completed", "service_tier": "flex",
            "output": [{"type": "web_search_call", "status": "completed", "action": {
                "type": "search", "sources": [{"type": "url", "url": SOURCE_URL}]}},
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                    "review": review_payload(decision), "suggested_title": "Cómo renovar su residencia en España"}),
                    "annotations": []}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500,
                      "output_tokens_details": {"reasoning_tokens": 250}}, **overrides}


class Session:
    def __init__(self, result=None, *, status=200, exception=None, before=None):
        self.body = result if result is not None else body()
        self.status, self.exception, self.before = status, exception, before
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.before:
            self.before()
        if self.exception:
            raise self.exception
        return SimpleNamespace(status_code=self.status, headers={"x-request-id": "req_assessment"},
                               json=lambda: self.body, close=lambda: None)


def execute(corpus, session, **kwargs):
    path, env = corpus
    return run_assessment(path, 1, 3, MODEL, "dnv-automatic-assessment", env_path=env, session=session, **kwargs)


def output_json(response):
    return json.loads(response["output"][-1]["content"][0]["text"])


def set_output(response, data):
    response["output"][-1]["content"][0]["text"] = json.dumps(data)
    return response


def test_automatic_assessment_uses_only_corpus_and_template_no_manual_decisions(corpus):
    path, _ = corpus
    def reserved():
        assert get_assessment(path, 1)["status"] == "running"
    session = Session(before=reserved)
    result = execute(corpus, session)
    assert result["status"] == "completed" and result["decision"]["decision"] == "adapt"
    assert result["review"]["source"] == "model"
    assert result["review"]["reviewer"] == "OpenAI: " + MODEL
    assert result["review"]["assessment_id"] == result["id"]
    assert result["suggested_title"] == "Cómo renovar su residencia en España"
    assert result["web_search_calls"] == 1 and result["usage"]["output_tokens"] == 500
    method, url, request = session.calls[0]
    assert method == "POST" and url == "https://api.openai.com/v1/responses"
    assert request["allow_redirects"] is False and request["verify"] is True
    assert request["headers"]["Authorization"] == "Bearer " + SECRET
    payload = request["json"]
    assert payload["model"] == MODEL and payload["reasoning"] == {"effort": "high"}
    assert payload["max_tool_calls"] == 3 and payload["max_output_tokens"] == 12000
    assert payload["service_tier"] == "flex" and payload["store"] is False
    assert payload["text"]["format"]["strict"] is True
    assert payload["include"] == ["web_search_call.action.sources"]
    assert "inclusion.gob.es" in payload["tools"][0]["filters"]["allowed_domains"]
    assert "Renewing a digital nomad" in payload["input"] and "es-CO" in payload["input"]
    assert "MANUAL_" not in json.dumps(payload) and "WITHHELD" not in json.dumps(payload)
    assert SECRET not in json.dumps(result) and SECRET.encode() not in path.read_bytes()
    assert applicability.require_review(path, 1, 3)["id"] == result["review"]["id"]


def test_repeated_assessment_has_no_paid_request_and_does_not_reactivate_old_review(corpus):
    path, env = corpus
    session = Session()
    first = execute(corpus, session)
    env.unlink()
    pending = review_payload("needs_verification")
    pending["reviewer"] = "Later review"
    applicability.save_review(path, 1, 3, pending)
    second = execute(corpus, session)
    assert first["id"] == second["id"] and len(session.calls) == 1
    assert applicability.review_status(path, 1, 3)["allowed"] is False


def test_concurrency_only_reserves_one_paid_assessment(corpus):
    session = Session()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(corpus, session), range(2)))
    assert len(session.calls) == 1
    assert len({result["id"] for result in results}) == 1


def category_catalog():
    return {"target_id": 3, "catalog_revision": "catalog-revision", "policy_revision": "policy-revision",
            "excluded_default_category_id": 1, "candidates": [
                {"id": 811, "name": "Residencia", "slug": "residencia", "description": "Permisos", "parent": 0},
                {"id": 913, "name": "Teletrabajo", "slug": "teletrabajo", "description": "Trabajo remoto", "parent": 0}]}


def test_dynamic_categories_share_one_assessment_request_and_preserve_provenance(corpus):
    response = body()
    data = {**output_json(response), "selected_category_ids": [913, 811]}
    session = Session(set_output(response, data))
    result = execute(corpus, session, category_context=category_catalog())
    assert result["status"] == "completed" and len(session.calls) == 1
    selection = result["category_selection"]
    assert selection == {**category_catalog(), "selected_category_ids": [811, 913], "source": "model"}
    assert get_assessment(corpus[0], result["id"])["category_selection"] == selection
    assert "selected_category_ids" not in result["decision"]
    assert result["review"]["assessment_id"] == result["id"]
    payload = session.calls[0][2]["json"]
    assert json.loads(payload["input"])["wordpress_categories"] == category_catalog()
    schema = payload["text"]["format"]["schema"]
    assert schema["properties"]["selected_category_ids"]["items"]["enum"] == [811, 913]
    assert "selected_category_ids" in schema["required"]
    assert payload["max_tool_calls"] == 3


@pytest.mark.parametrize("selected", [[999], [1], [True], [811, 811], [811, 913, 811, 913], "811"])
def test_invalid_model_categories_are_durable_without_retry(corpus, selected):
    response = body()
    session = Session(set_output(response, {**output_json(response), "selected_category_ids": selected}))
    first = execute(corpus, session, category_context=category_catalog())
    second = execute(corpus, session, category_context=category_catalog())
    assert first["status"] == second["status"] == "failed"
    assert first["response"] and not first.get("review")
    assert len(session.calls) == 1


@pytest.mark.parametrize("decision", ["adapt", "omit", "needs_verification"])
def test_empty_categories_do_not_invalidate_editorial_decision(corpus, decision):
    response = body(decision)
    session = Session(set_output(response, {**output_json(response), "selected_category_ids": []}))
    result = execute(corpus, session, category_context=category_catalog())
    assert result["status"] == "completed" and result["decision"]["decision"] == decision
    assert result["category_selection"]["selected_category_ids"] == []


def test_legacy_assessment_is_not_regenerated_for_new_category_catalog(corpus):
    session = Session()
    first = execute(corpus, session)
    second = execute(corpus, session, category_context=category_catalog())
    assert first["id"] == second["id"] and second["category_selection"] == {}
    assert len(session.calls) == 1


def test_changed_taxonomy_does_not_repeat_cached_paid_assessment(corpus):
    response = body()
    session = Session(set_output(response, {**output_json(response), "selected_category_ids": [811]}))
    original = execute(corpus, session, category_context=category_catalog())
    changed = {**category_catalog(), "catalog_revision": "new-catalog", "policy_revision": "new-policy"}
    cached = execute(corpus, session, category_context=changed)
    assert cached["category_selection"] == original["category_selection"]
    assert len(session.calls) == 1


def test_legacy_assessment_table_migrates_only_for_new_request(corpus):
    from src.generation.assessment import _SCHEMA
    path, _ = corpus
    old_schema = _SCHEMA.replace("    category_selection_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(category_selection_json)),\n", "")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(old_schema)
    result = execute(corpus, Session())
    assert result["status"] == "completed" and result["category_selection"] == {}
    with closing(sqlite3.connect(path)) as db:
        assert "category_selection_json" in {row[1] for row in db.execute("PRAGMA table_info(generation_assessments)")}


@pytest.mark.parametrize("decision", ["omit", "needs_verification"])
def test_negative_decisions_are_preserved_and_block_drafting(corpus, decision):
    result = execute(corpus, Session(body(decision)))
    assert result["status"] == "completed" and result["decision"]["decision"] == decision
    assert result["review"]["source"] == "model"
    assert not applicability.review_status(corpus[0], 1, 3)["allowed"]


@pytest.mark.parametrize("change", ["no_search", "invented_url", "unofficial", "wrong_date", "no_evidence", "wrong_model", "bad_title", "bad_json"])
def test_unverified_or_invalid_model_output_cannot_become_a_review(corpus, change):
    response = body()
    data = output_json(response)
    if change == "no_search":
        response["output"] = response["output"][1:]
    elif change == "invented_url":
        data["review"]["evidence"][0]["url"] = "https://www.inclusion.gob.es/invented"
    elif change == "unofficial":
        url = "https://inclusion.gob.es.evil.test/article"
        response["output"][0]["action"]["sources"][0]["url"] = url
        data["review"]["evidence"][0]["url"] = url
    elif change == "wrong_date":
        data["review"]["evidence"][0]["checked_at"] = "2020-01-01"
    elif change == "no_evidence":
        data["review"]["evidence"] = []
    elif change == "wrong_model":
        response["model"] = "gpt-5-nano"
    elif change == "bad_title":
        data["suggested_title"] = "<script>bad</script>"
    set_output(response, data)
    if change == "bad_json":
        response["output"][-1]["content"][0]["text"] = "not JSON"
    result = execute(corpus, Session(response))
    assert result["status"] == "failed" and result["response"]
    assert result["decision"] == {} and "review" not in result
    with closing(sqlite3.connect(corpus[0])) as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_applicability_reviews").fetchone()[0] == 1


def test_citation_annotations_are_valid_evidence_when_search_completed(corpus):
    response = body()
    response["output"][0]["action"]["sources"] = []
    response["output"][-1]["content"][0]["annotations"] = [{"type": "url_citation", "url": SOURCE_URL}]
    assert execute(corpus, Session(response))["status"] == "completed"


def test_timeout_keeps_unknown_record_no_retry_no_secret(corpus):
    session = Session(exception=requests.Timeout(SECRET))
    result = execute(corpus, session)
    assert result["status"] == "unknown" and SECRET not in json.dumps(result)
    assert execute(corpus, session)["id"] == result["id"] and len(session.calls) == 1


def test_interruption_keeps_reservation(corpus):
    session = Session(exception=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        execute(corpus, session)
    assert execute(corpus, session)["status"] == "running" and len(session.calls) == 1


@pytest.mark.parametrize("http,expected", [(401,"failed"),(429,"failed"),(302,"unknown"),(408,"unknown"),(500,"unknown")])
def test_http_failures_do_not_save_body_or_retry(corpus, http, expected):
    session = Session({"error": SECRET}, status=http)
    result = execute(corpus, session)
    assert result["status"] == expected and result["response"] == {}
    assert SECRET not in json.dumps(result)
    assert execute(corpus, session)["id"] == result["id"] and len(session.calls) == 1


@pytest.mark.parametrize("status", ["incomplete", "failed", "in_progress"])
def test_nonfinal_response_never_creates_approval(corpus, status):
    result = execute(corpus, Session(body(status=status)))
    assert result["status"] == ("unknown" if status == "in_progress" else status)
    assert result["response"] and not result["decision"] and "review" not in result


def test_source_change_during_http_preserves_response_without_approving_new_context(corpus):
    path, _ = corpus
    def change():
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE generation_destinations SET notes='Changed during request' WHERE id=3")
    result = execute(corpus, Session(before=change))
    assert result["status"] == "completed" and result["decision"]["decision"] == "adapt"
    assert "review_error" in result and "review" not in result
    assert not applicability.review_status(path, 1, 3)["allowed"]


def test_options_mismatch_never_reuses_paid_assessment_as_different_request(corpus):
    session = Session()
    execute(corpus, session)
    for options in ({"reasoning_effort":"low"},{"service_tier":"default"},{"max_tool_calls":2},{"max_output_tokens":4000}):
        with pytest.raises(ValueError, match="otra solicitud"):
            execute(corpus, session, **options)
    assert len(session.calls) == 1


def test_disabled_destination_and_secret_in_source_fail_before_http(corpus):
    path, _ = corpus
    session = Session()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE generation_destinations SET enabled=0 WHERE id=3")
    with pytest.raises(ValueError, match="habilitados"):
        execute(corpus, session)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE generation_destinations SET enabled=1,notes=? WHERE id=3", (SECRET,))
    with pytest.raises(ValueError, match="autenticación"):
        execute(corpus, session)
    assert not session.calls


def test_output_none_is_archived_as_failure_not_an_exception(corpus):
    result = execute(corpus, Session(body(output=None)))
    assert result["status"] == "failed" and result["response"]["output"] is None
    assert result["web_search_calls"] == 0
