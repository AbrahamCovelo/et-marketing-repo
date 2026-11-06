"""The engine's formatting contract covers all destinations and custom prompts."""
import re

import pytest

from src.deployment.content import ALLOWED_TAGS, validate_semantic_html
from src.deployment.repository import CTA_NAMES
from src.generation.output_contract import OUTPUT_CONTRACT_VERSION, output_instructions
from src.generation.repository import GenerationRepository
from src.generation.applicability import save_review
from src.generation.trial import run_trial
from test_generation_trial import corpus, Session, MODEL


@pytest.mark.parametrize("locale", list(CTA_NAMES))
def test_marker_examples_are_valid_and_exclusive_to_destination(locale):
    rules = output_instructions(locale)
    examples = re.findall(r"<p>\[CTA_[A-Z_]+\]</p>", rules)
    assert examples == [f"<p>[{marker}]</p>" for marker in CTA_NAMES[locale]]
    for example in examples:
        assert validate_semantic_html(example + "<p>Editorial text.</p>")["valid"]
    for other_locale, markers in CTA_NAMES.items():
        if other_locale != locale:
            assert all(marker not in rules for marker in markers)


def test_contract_restricts_serialization_without_rewriting_editorial_task():
    rules = output_instructions("es-CO")
    assert OUTPUT_CONTRACT_VERSION in rules
    assert "preserve the approved scope, meaning, facts, language" in rules
    assert "NEVER put a marker on a bare line outside HTML" in rules
    assert "No bare text directly inside list containers" in rules
    allowed_line = next(line for line in rules.splitlines() if line.startswith("Allowed tags:"))
    assert set(re.findall(r"<([a-z0-9]+)>", allowed_line)) == ALLOWED_TAGS


def test_unknown_locale_does_not_invent_markers():
    rules = output_instructions("de-DE")
    assert "omit CTAs" in rules
    assert not re.findall(r"\[CTA_[A-Z_]+\]", rules)


def test_contract_is_in_request_and_snapshot_without_mutating_prompt(corpus):
    path, env = corpus
    repository = GenerationRepository(path)
    before = repository.configuration_context(3)
    session = Session()
    trial = run_trial(path, 1, 3, MODEL, "format-contract", env_path=env, session=session)
    instructions = session.calls[0][2]["json"]["instructions"]
    assert instructions.endswith(output_instructions("es-CO"))
    assert trial["snapshot"]["output_contract_version"] == OUTPUT_CONTRACT_VERSION
    assert before == repository.configuration_context(3)
    repeated = run_trial(path, 1, 3, MODEL, "format-contract", env_path=env, session=session)
    assert repeated == trial and len(session.calls) == 1


def test_custom_editorial_template_still_receives_engine_contract(corpus):
    path, env = corpus
    repository = GenerationRepository(path)
    context = repository.configuration_context(3)
    prompt = repository.save_prompt({"name": "Custom template", "description": "",
        "system_prompt": "EDITORIAL_CUSTOM_ROLE", "template": "Write from {{article_text}}.",
        "status": "draft", "archived": False})
    current = context["configuration"]
    payload = {key: current[key] for key in ("name", "destination_id", "provider", "model", "notes", "enabled")}
    payload["prompt_version_id"] = prompt["version_id"]
    repository.save_configuration(payload, 3)
    save_review(path, 1, 3, {
        "decision": "adapt", "reason": "El transporte urbano es aplicable al público colombiano.",
        "central_question": "¿Cómo funciona el transporte?", "reviewer": "Offline test",
        "excluded_sections": [], "evidence": [],
        "checks": {"central_applicable": True, "whole_article_exclusive": False,
                   "alternative_applicable": None, "essential_information_verified": True},
    })
    session = Session()
    trial = run_trial(path, 1, 3, MODEL, "format-custom", env_path=env, session=session)
    request = session.calls[0][2]["json"]
    assert "EDITORIAL_CUSTOM_ROLE" in request["instructions"]
    assert request["instructions"].endswith(output_instructions("es-CO"))
    assert "Write from" in request["input"]
    assert trial["snapshot"]["output_contract_version"] == OUTPUT_CONTRACT_VERSION
