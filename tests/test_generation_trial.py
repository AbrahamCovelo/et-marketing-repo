"""One paid request at most, with durable source and output provenance."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest
import requests

from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.presets import load_editorial_prompts
from src.generation.trial import get_trial, list_trials, run_trial


SECRET = "sk-test-secret-never-persist-this-value"
MODEL = "gpt-5-nano-2025-08-07"


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "articles.db"
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={SECRET}\n", encoding="utf-8")
    with Database(path) as db:
        source = ExtractionResult(url="https://movingtospain.com/barcelona/",
            canonical_url="https://movingtospain.com/barcelona/", title="Moving to Barcelona",
            original_text="WITHHELD BYLINE",
            blocks=[{"key": "b1", "position": 0, "source_order": 1, "kind": "paragraph",
                     "text": "Barcelona has public transport.", "section_path": [], "data": {}, "flags": []}])
        db.store_article(source, raw_html="<main>WITHHELD RAW HTML</main>")
    load_editorial_prompts(path)
    return path, env


def result_body(**overrides):
    return {
        "id": "resp_test", "model": MODEL, "status": "completed",
        "output": [{"type": "reasoning", "summary": []}, {"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "<p>Barcelona tiene transporte público.</p>"}]}],
        "usage": {"input_tokens": 450, "output_tokens": 85, "total_tokens": 535,
                  "input_tokens_details": {"cached_tokens": 12}, "output_tokens_details": {"reasoning_tokens": 30}},
        **overrides,
    }


class Session:
    def __init__(self, body=None, status=200, exception=None, before=None):
        self.body = result_body() if body is None else body
        self.status, self.exception, self.before = status, exception, before
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.before:
            self.before()
        if self.exception:
            raise self.exception
        return SimpleNamespace(status_code=self.status, headers={"x-request-id": "req_test"}, json=lambda: self.body)


def execute(corpus, session, **kwargs):
    path, env = corpus
    return run_trial(path, 1, 3, MODEL, "colombia-barcelona-test-1", env_path=env, session=session, **kwargs)


def test_trial_stores_pinned_input_output_usage_without_secrets(corpus):
    path, _ = corpus
    session = Session(before=lambda: assert_reserved(path))
    trial = execute(corpus, session)
    assert trial["status"] == "completed"
    assert trial["source_version_id"] == 1 and trial["configuration_id"] == 3
    assert trial["response_id"] == "resp_test" and trial["response_model"] == MODEL
    assert trial["html"].startswith("<p>Barcelona")
    assert trial["usage"]["output_tokens_details"]["reasoning_tokens"] == 30
    assert trial["usage"]["input_tokens_details"]["cached_tokens"] == 12
    request = session.calls[0][2]
    assert session.calls[0][:2] == ("POST", "https://api.openai.com/v1/responses")
    assert request["allow_redirects"] is False
    assert request["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert request["json"]["store"] is False
    assert request["json"]["reasoning"] == {"effort": "minimal"}
    assert request["json"]["max_output_tokens"] == 8000
    assert request["json"]["service_tier"] == "flex"
    assert request["timeout"] == (10, 600)
    assert trial["request"] == request["json"]
    assert "Barcelona has public transport." in trial["request"]["input"]
    saved = json.dumps(trial)
    assert "WITHHELD" not in saved and SECRET not in saved
    assert SECRET.encode() not in path.read_bytes()
    assert get_trial(path, trial["id"]) == trial
    assert list_trials(path, article_id=1) == [trial]
    assert list_trials(path, article_id=99) == []


def assert_reserved(path):
    rows = list_trials(path)
    assert len(rows) == 1 and rows[0]["status"] == "running"


def test_duplicate_key_reuses_even_without_credentials_or_live_current_prompt(corpus):
    path, env = corpus
    session = Session()
    first = execute(corpus, session)
    env.unlink()
    second = execute(corpus, session)
    assert second == first and len(session.calls) == 1
    with pytest.raises(ValueError, match="ya pertenece"):
        run_trial(path, 2, 3, MODEL, first["run_key"], env_path=env, session=session)
    with pytest.raises(ValueError, match="ya pertenece"):
        run_trial(path, 1, 3, "gpt-6-luna", first["run_key"], env_path=env, session=session)
    with pytest.raises(ValueError, match="nivel de servicio"):
        execute(corpus, session, service_tier="default")


def test_concurrent_calls_reserve_only_once(corpus):
    session = Session()
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: execute(corpus, session), range(2)))
    assert len(session.calls) == 1
    assert len({row["id"] for row in rows}) == 1


def test_timeout_is_unknown_and_cannot_retry_or_leak_error(corpus):
    session = Session(exception=requests.Timeout(f"timeout with {SECRET}"))
    first = execute(corpus, session)
    assert first["status"] == "unknown" and "Puede haber consumo" in first["error"]
    assert SECRET not in json.dumps(first)
    assert execute(corpus, session) == first and len(session.calls) == 1


def test_process_interruption_keeps_durable_running_record(corpus):
    session = Session(exception=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        execute(corpus, session)
    assert execute(corpus, session)["status"] == "running"
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_error_never_saves_response_body_or_retries(corpus, status):
    session = Session(body={"error": f"Invalid key {SECRET}"}, status=status)
    trial = execute(corpus, session)
    assert trial["status"] == "failed" and str(status) in trial["error"]
    assert trial["response"] == {} and SECRET not in json.dumps(trial)
    assert execute(corpus, session) == trial and len(session.calls) == 1


def test_incomplete_response_keeps_partial_text_and_usage(corpus):
    session = Session(body=result_body(status="incomplete", incomplete_details={"reason": "max_output_tokens"}))
    trial = execute(corpus, session)
    assert trial["status"] == "incomplete"
    assert trial["html"] and trial["usage"]["total_tokens"] == 535
    assert trial["response"]["incomplete_details"]["reason"] == "max_output_tokens"
    execute(corpus, session)
    assert len(session.calls) == 1


@pytest.mark.parametrize("output", [[], None, {"wrong": "shape"}, [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]])
def test_completed_without_text_is_not_success(corpus, output):
    trial = execute(corpus, Session(body=result_body(output=output)))
    assert trial["status"] == "failed" and not trial["html"]
    assert trial["usage"] and "sin devolver texto" in trial["error"]


def test_malformed_response_is_unknown(corpus):
    trial = execute(corpus, Session(body=["not an object"]))
    assert trial["status"] == "unknown"


def test_successful_response_redacts_any_echoed_credential(corpus):
    trial = execute(corpus, Session(body=result_body(extra={"echo": SECRET})))
    assert SECRET not in json.dumps(trial)
    assert trial["response"]["extra"]["echo"] == "[credential redacted]"


def test_invalid_credentials_reserve_nothing_and_readers_never_create_database(corpus, tmp_path):
    path, env = corpus
    env.unlink()
    with pytest.raises(ValueError, match="credencial"):
        execute(corpus, Session())
    assert list_trials(path) == []
    missing = tmp_path / "missing.db"
    assert list_trials(missing) == [] and not missing.exists()
    with pytest.raises(KeyError):
        get_trial(missing, 1)
    assert not missing.exists()
