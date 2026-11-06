"""Selected reasoning effort reaches both paid-request builders unchanged.

All transport is fake; these tests never request provider inference.
"""
import pytest

from src.generation.assessment import run_assessment
from src.generation.trial import run_trial
from test_generation_trial import corpus, Session as TrialSession, result_body
from test_assessment import Session as AssessmentSession


MODEL = "gpt-6-luna"


@pytest.mark.parametrize("effort", ["", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"])
def test_trial_keeps_dynamic_effort_in_payload_and_journal(corpus, effort):
    path, env = corpus
    session = TrialSession(body=result_body(model=MODEL))
    result = run_trial(path, 1, 3, MODEL, "dynamic-effort", reasoning_effort=effort, env_path=env, session=session)
    assert result["status"] == "completed"
    payload = session.calls[0][2]["json"]
    assert payload["model"] == MODEL
    if effort:
        assert payload["reasoning"] == {"effort": effort}
    else:
        assert "reasoning" not in payload
    assert result["request"] == payload


@pytest.mark.parametrize("effort", ["", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"])
def test_assessment_keeps_dynamic_effort_in_payload_and_journal(corpus, effort):
    path, env = corpus
    session = AssessmentSession()
    result = run_assessment(path, 1, 3, MODEL, "dynamic-assessment-effort", reasoning_effort=effort, env_path=env, session=session)
    assert result["status"] == "completed"
    payload = session.calls[0][2]["json"]
    assert payload["model"] == MODEL
    if effort:
        assert payload["reasoning"] == {"effort": effort}
    else:
        assert "reasoning" not in payload
    assert result["reasoning_effort"] == effort and result["request"] == payload
    repeated = run_assessment(path, 1, 3, MODEL, "dynamic-assessment-effort", reasoning_effort=effort, env_path=env, session=session)
    assert repeated["id"] == result["id"] and len(session.calls) == 1


def test_explicit_provider_default_suppresses_legacy_nano_minimal_override(corpus):
    path, env = corpus
    session = TrialSession()
    result = run_trial(path, 1, 3, "gpt-5-nano-2025-08-07", "nano-provider-default",
                       reasoning_effort="", env_path=env, session=session)
    assert result["status"] == "completed"
    assert "reasoning" not in session.calls[0][2]["json"]


@pytest.mark.parametrize("effort", ["High", "invented-effort", True, {"effort": "high"}])
def test_invalid_effort_never_reaches_either_provider_builder(corpus, effort):
    path, env = corpus
    for function, session in [(run_trial, TrialSession()), (run_assessment, AssessmentSession())]:
        with pytest.raises(ValueError):
            function(path, 1, 3, MODEL, "invalid-effort", reasoning_effort=effort, env_path=env, session=session)
        assert session.calls == []
