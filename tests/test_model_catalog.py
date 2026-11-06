"""Model discovery uses GET only and never invents paid-probe capabilities."""
import json

import pytest
import requests

from src.generation.model_catalog import (
    DOCS_ROOT, MODELS_URL, get_catalog, get_model_capabilities,
    parse_capabilities, validate_model_selection,
)
from src.generation.model_options import MODEL_REFERENCE


MODEL = "gpt-99-example"
DOC = """# Example
Model ID: `gpt-99-example`
`reasoning.effort` supports `low`, `medium` (default), `high`,
`xhigh`, and `max`.

## Model details
- Input modalities: text, image
- Output modalities: text
- Reasoning token support

## Endpoints
| Endpoint | Route | Support |
| --- | --- | --- |
| Responses | `v1/responses` | Supported |

## Supported features
- streaming
- structured_outputs
- web_search

## Supported tools
- web_search
- file_search

## Snapshots
- `gpt-99-example-2026-10-01`
"""


class Reply:
    def __init__(self, payload=None, *, text="", status=200):
        self.payload = payload
        self.content = text.encode("utf-8")
        self.status_code = status

    def json(self):
        return self.payload


class GetOnly:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected network request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def post(self, *args, **kwargs):
        raise AssertionError("Catalog must never make a paid generation call")


@pytest.fixture
def options(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=private-key-not-for-output\n", encoding="utf-8")
    return {"env_path": env, "cache_path": tmp_path / "model-catalog.json"}


def listing(*ids):
    return Reply({"data": [{"id": identifier, "created": 123, "shutdown_date": None} for identifier in ids]})


def test_no_hardcoded_model_ids_and_preparation_remains_offline():
    assert MODEL_REFERENCE["models"] == []
    assert MODEL_REFERENCE["dynamic"] is True


def test_available_models_from_account_get_without_paid_call(options):
    session = GetOnly(listing(MODEL, "gpt-image-example", "gpt-audio-example", "embedding-model", MODEL))
    result = get_catalog(**options, session=session)
    assert [m["id"] for m in result["models"]] == [MODEL]
    assert result["availability_checked"] is True
    assert result["status"] == "available"
    assert session.calls[0][0] == MODELS_URL
    assert session.calls[0][1]["allow_redirects"] is False
    assert "private-key-not-for-output" not in json.dumps(result)
    assert "private-key-not-for-output" not in options["cache_path"].read_text()
    assert result["models"][0]["reasoning_efforts"] is None


def test_models_cache_avoids_network_until_explicit_refresh(options):
    session = GetOnly(listing(MODEL), listing("gpt-100-new"))
    get_catalog(**options, session=session)
    assert get_catalog(**options, session=session)["cached"] is True
    updated = get_catalog(**options, refresh=True, session=session)
    assert updated["models"][0]["id"] == "gpt-100-new"
    assert len(session.calls) == 2


def test_shutdown_model_excluded_and_future_shutdown_retained(options):
    session = GetOnly(Reply({"data": [
        {"id": "gpt-1-dead", "shutdown_date": "2000-01-01"},
        {"id": MODEL, "shutdown_date": "2999-01-01"},
    ]}))
    assert [m["id"] for m in get_catalog(**options, session=session)["models"]] == [MODEL]


def test_missing_credential_never_contacts_provider(options):
    options["env_path"].write_text("")
    result = get_catalog(**options, session=GetOnly())
    assert result["status"] == "unconfigured" and result["models"] == []


def test_changed_credential_cannot_reuse_previous_accounts_models(options):
    get_catalog(**options, session=GetOnly(listing(MODEL)))
    options["env_path"].write_text("OPENAI_API_KEY=new-key")
    result = get_catalog(**options, session=GetOnly(Reply(status=401)))
    assert result["models"] == [] and result["status"] == "unavailable"


@pytest.mark.parametrize("failure", [Reply(status=401), Reply(status=302), Reply(payload={"bad": []}),
                                      requests.ConnectionError("private-key-not-for-output")])
def test_errors_retain_cache_mark_stale_without_secret_leak(options, failure):
    get_catalog(**options, session=GetOnly(listing(MODEL)))
    result = get_catalog(**options, refresh=True, session=GetOnly(failure))
    assert result["stale"] is True and result["status"] == "stale"
    assert "private-key-not-for-output" not in json.dumps(result)


def test_documented_efforts_and_required_capabilities_parsed():
    result = parse_capabilities(MODEL, DOC, DOCS_ROOT + MODEL)
    assert result["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert result["default_reasoning_effort"] == "medium"
    assert result["responses"] is True
    assert result["text_output"] is True
    assert result["structured_outputs"] is True
    assert result["web_search"] is True
    assert result["automatic_supported"] is True


def test_non_reasoning_doc_yields_empty_efforts_and_no_family_guess():
    document = DOC.replace("`reasoning.effort` supports `low`, `medium` (default), `high`,\n`xhigh`, and `max`.",
                           "A non-reasoning model.")
    result = parse_capabilities(MODEL, document, DOCS_ROOT + MODEL)
    assert result["reasoning_efforts"] == []
    assert result["reasoning_supported"] is False


def test_unenumerated_reasoning_efforts_remain_unknown():
    document = DOC.replace("`reasoning.effort` supports `low`, `medium` (default), `high`,\n`xhigh`, and `max`.", "")
    result = parse_capabilities(MODEL, document, DOCS_ROOT + MODEL)
    assert result["reasoning_efforts"] is None
    assert result["reasoning_supported"] is None


def test_unsupported_endpoint_disables_automatic_generation():
    result = parse_capabilities(MODEL, DOC.replace("| Supported |", "| Not supported |"), DOCS_ROOT + MODEL)
    assert result["automatic_supported"] is False


def test_mismatched_doc_identity_not_used():
    with pytest.raises(ValueError):
        parse_capabilities("gpt-98-other", DOC, DOCS_ROOT + MODEL)


def test_capabilities_get_is_unauthenticated_and_cached(options):
    session = GetOnly(Reply(text=DOC))
    result = get_model_capabilities(MODEL, cache_path=options["cache_path"], session=session)
    assert result["reasoning_efforts"][-1] == "max"
    assert session.calls[0][0] == DOCS_ROOT + MODEL + ".md"
    assert "Authorization" not in session.calls[0][1]["headers"]
    assert get_model_capabilities(MODEL, cache_path=options["cache_path"], session=session)["cached"] is True


def test_dated_snapshot_fallback_requires_exact_listing(options):
    snapshot = MODEL + "-2026-10-01"
    session = GetOnly(Reply(status=404), Reply(text=DOC))
    result = get_model_capabilities(snapshot, cache_path=options["cache_path"], session=session)
    assert result["status"] == "verified" and result["id"] == snapshot
    other = get_model_capabilities(MODEL + "-2026-10-02", cache_path=options["cache_path"],
                                   session=GetOnly(Reply(status=404), Reply(text=DOC)))
    assert other["status"] == "unknown" and other["reasoning_efforts"] is None


def test_unknown_doc_does_not_invent_efforts(options):
    result = get_model_capabilities(MODEL, cache_path=options["cache_path"], session=GetOnly(Reply(status=404)))
    assert result["status"] == "unknown" and result["reasoning_efforts"] is None
    assert result["automatic_supported"] is True


def test_unknown_docs_are_briefly_cached_without_repeated_network(options, monkeypatch):
    session = GetOnly(Reply(status=404), Reply(text=DOC))
    first = get_model_capabilities(MODEL, cache_path=options["cache_path"], session=session)
    repeated = get_model_capabilities(MODEL, cache_path=options["cache_path"], session=session)
    assert first["status"] == repeated["status"] == "unknown"
    assert repeated["cached"] is True and len(session.calls) == 1
    data = json.loads(options["cache_path"].read_text())
    data["model:" + MODEL]["fetched_at"] -= 301
    options["cache_path"].write_text(json.dumps(data))
    refreshed = get_model_capabilities(MODEL, cache_path=options["cache_path"], session=session)
    assert refreshed["status"] == "verified" and len(session.calls) == 2


def test_explicit_doc_refresh_bypasses_negative_cache(options):
    session = GetOnly(Reply(status=404), Reply(text=DOC))
    get_model_capabilities(MODEL, cache_path=options["cache_path"], session=session)
    result = get_model_capabilities(MODEL, cache_path=options["cache_path"], refresh=True, session=session)
    assert result["status"] == "verified" and len(session.calls) == 2


@pytest.mark.parametrize("bad_record", [[], "broken", {"capabilities": []}, {"capabilities": {"id": "other"}}])
def test_malformed_cache_entries_are_discarded(options, bad_record):
    options["cache_path"].write_text(json.dumps({"version": 1, "catalog": bad_record,
                                                "model:" + MODEL: bad_record}))
    assert get_catalog(**options, session=GetOnly(listing(MODEL)))["status"] == "available"
    assert get_model_capabilities(MODEL, cache_path=options["cache_path"], session=GetOnly(Reply(text=DOC)))["status"] == "verified"


@pytest.mark.parametrize("model_id", ["gpt-/../../key", "https://evil.invalid", "gpt-99:bad", "gpt-99?bad"])
def test_identifiers_cannot_change_documentation_origin(options, model_id):
    with pytest.raises(ValueError):
        get_model_capabilities(model_id, cache_path=options["cache_path"], session=GetOnly())


def test_preflight_preserves_exact_model_and_chosen_effort(options):
    result = validate_model_selection(MODEL, "high", **options,
                                      session=GetOnly(listing(MODEL), Reply(text=DOC)))
    assert result["model"] == MODEL and result["reasoning_effort"] == "high"


def test_preflight_rejects_undocumented_effort(options):
    with pytest.raises(ValueError, match="esfuerzo"):
        validate_model_selection(MODEL, "minimal", **options,
                                 session=GetOnly(listing(MODEL), Reply(text=DOC)))


def test_preflight_unknown_capabilities_only_allows_provider_default(options):
    result = validate_model_selection(MODEL, None, **options,
                                      session=GetOnly(listing(MODEL), Reply(status=404)))
    assert result["reasoning_effort"] is None
    with pytest.raises(ValueError, match="esfuerzo"):
        validate_model_selection(MODEL, "high", **options, session=GetOnly(Reply(status=404)))


def test_preflight_refuses_model_not_in_account(options):
    with pytest.raises(ValueError, match="lista disponible"):
        validate_model_selection("gpt-98-absent", None, **options, session=GetOnly(listing(MODEL)))


def test_preflight_refuses_known_incompatible_model_before_generation(options):
    with pytest.raises(ValueError, match="funciones necesarias"):
        validate_model_selection(MODEL, None, **options,
                                 session=GetOnly(listing(MODEL), Reply(text=DOC.replace("- web_search", "- file_uploads"))))
