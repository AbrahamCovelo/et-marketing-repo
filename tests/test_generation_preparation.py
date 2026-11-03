"""Preparation reads sanitized source material and cannot call an AI provider."""
from __future__ import annotations

import json
import socket
import sqlite3

import pytest
import requests

from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.preparation import (
    GenerationDisabledError, PLACEHOLDERS, execute_generation,
    preview_preparation, render_template,
)
from src.generation.repository import GenerationRepository
from src.generation.settings import public_status


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La preparación local intentó usar la red.")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "source.db"
    with Database(path) as database:
        for number in (1, 2):
            url = f"https://movingtospain.com/guide-{number}/"
            article = ExtractionResult(
                url=url, canonical_url=url, title=f"Guide {number}",
                metadata={"authors": ["QUARANTINED AUTHOR"], "reviewers": ["PRIVATE REVIEWER"]},
                original_text="QUARANTINED ORIGINAL TEXT",
                blocks=[{
                    "key": "b1", "position": 0, "source_order": 1, "kind": "paragraph",
                    "text": "Clean source with literal {{destination_name}}.",
                    "data": {}, "flags": [], "section_path": [],
                }],
                links=[{
                    "block_key": "b1", "source_order": 1, "url": "https://www.boe.es/example",
                    "anchor": "Official source", "kind": "external_reference", "disposition": "retained",
                }],
                events=[{
                    "source_order": 2, "reason": "editorial_quote", "text": "QUARANTINED QUOTE",
                    "requires_review": True, "prev_block_key": "b1", "next_block_key": None,
                }],
                quality_flags=["editorial_review_required"],
            )
            database.store_article(article, raw_html="<html>PRIVATE RAW CONTENT</html>")
    repository = GenerationRepository(path)
    return path, repository


def _database_snapshot(path):
    with sqlite3.connect(path) as connection:
        return "\n".join(connection.iterdump())


def _reviewed_configuration(repository):
    destination = repository.catalog()["destinations"][0]
    repository.save_prompt({
        "name": "Reviewed local template", "description": "A test template",
        "system_prompt": "Prepare text for {{destination_name}} in {{locale}}.",
        "template": 'Title: {{article_title}}\nSource: {{article_url}}\n{{article_text}}\n{{article_json}}\n{"json":{"value":1}}',
        "status": "reviewed", "archived": False,
    })
    prompt = next(row for row in repository.catalog()["prompts"] if row["name"] == "Reviewed local template")
    repository.save_configuration({
        "name": "Local reviewed configuration", "destination_id": destination["id"],
        "prompt_version_id": prompt["current_version_id"], "provider": "openai",
        "model": "test-model-never-called", "notes": "", "enabled": True,
    })
    configuration = next(row for row in repository.catalog()["configurations"] if row["name"] == "Local reviewed configuration")
    return prompt, configuration


def test_empty_drafts_preview_without_inventing_content_or_writing_plans(corpus):
    path, repository = corpus
    ids = [row["id"] for row in repository.catalog()["configurations"]]
    before = _database_snapshot(path)
    result = preview_preparation(path, [1, 2, 1], ids)
    assert result["mode"] == "preparation" and result["execution_enabled"] is False
    assert (result["article_count"], result["configuration_count"], result["pair_count"]) == (2, 3, 6)
    assert len(result["items"]) == 6
    assert result["example"]["rendered_prompt"] == ""
    assert result["example"]["system_prompt"] == ""
    assert any("borrador" in warning for warning in result["warnings"])
    assert any("modelo" in warning for warning in result["warnings"])
    assert all("source_context" not in item and "rendered_prompt" not in item for item in result["items"])
    assert _database_snapshot(path) == before


def test_preview_uses_sanitized_export_pinned_versions_and_single_pass(corpus):
    path, repository = corpus
    prompt, configuration = _reviewed_configuration(repository)
    repository.save_prompt({
        "name": prompt["name"], "description": "New revision",
        "system_prompt": "NEW INSTRUCTIONS", "template": "NEW VERSION {{article_text}}",
        "status": "reviewed", "archived": False,
    }, prompt_id=prompt["id"])
    before = _database_snapshot(path)
    result = preview_preparation(path, [1], [configuration["id"]])
    example = result["example"]
    text = json.dumps(result, ensure_ascii=False)
    assert "QUARANTINED" not in text and "PRIVATE REVIEWER" not in text and "PRIVATE RAW CONTENT" not in text
    assert "NEW INSTRUCTIONS" not in text and "NEW VERSION" not in text
    assert "Clean source with literal {{destination_name}}." in example["rendered_prompt"]
    assert '{"json":{"value":1}}' in example["rendered_prompt"]
    assert "https://www.boe.es/example" in text
    assert example["source_context"]["gaps"][0]["reason"] == "editorial_quote"
    assert example["prompt_version_id"] == prompt["current_version_id"]
    assert example["configuration_version_id"] == configuration["current_version_id"]
    assert example["source_version_id"] == example["source_context"]["article"]["version_id"]
    assert any("más reciente" in warning for warning in example["warnings"])
    assert any("origen" in warning for warning in example["warnings"])
    assert _database_snapshot(path) == before


@pytest.mark.parametrize("template", [
    "{{secret}}", "{{article_text}", "{{article_text", "{{{article_text}}}",
    "{article_text}}", "{{ article_text.upper() }}", "{% for article in articles %}",
    "{{__import__('os')}}", "{{article_text}}}", "{{article_text}} {{unknown}}",
])
def test_unknown_or_malformed_placeholders_fail_locally(template):
    with pytest.raises(ValueError):
        render_template(template, dict.fromkeys(PLACEHOLDERS, "example"))


def test_renderer_preserves_json_and_does_not_evaluate_source_content():
    values = dict.fromkeys(PLACEHOLDERS, "")
    values["article_text"] = "Literal {{locale}} ${HOME} {x}"
    assert render_template('JSON: {"a":{"b":2}} {{ article_text }}', values) == 'JSON: {"a":{"b":2}} Literal {{locale}} ${HOME} {x}'
    values["article_json"] = '{"content":{"body":"source"}}'
    assert json.loads(render_template('{"outer":{"source": {{article_json}}}}', values)) == {
        "outer": {"source": {"content": {"body": "source"}}},
    }


def test_renderer_rejects_source_amplification_before_building_output():
    values = dict.fromkeys(PLACEHOLDERS, "")
    values["article_json"] = "x" * 5001
    with pytest.raises(ValueError, match="5.000.000 caracteres"):
        render_template("{{article_json}}" * 1000, values)


def test_render_size_counts_literal_text_and_accepts_exact_limit(monkeypatch):
    monkeypatch.setattr("src.generation.preparation.MAX_RENDERED_CHARACTERS", 100)
    values = dict.fromkeys(PLACEHOLDERS, "")
    values["article_text"] = "x" * 30
    template = "prefix" + "{{ article_text }}" * 3 + "tail"
    assert render_template(template, values) == "prefix" + "x" * 90 + "tail"
    with pytest.raises(ValueError, match="tamaño máximo"):
        render_template(template + ".", values)


@pytest.mark.parametrize("articles, configurations", [
    ([], [1]), ([1], []), ("1", [1]), ([True], [1]), ([0], [1]), (["1"], [1]),
    ([1], [True]), ([1], [2**64]), ([1] * 501, [1]), (list(range(1, 252)), [1, 2]),
])
def test_preview_validates_selection_before_database_work(tmp_path, articles, configurations):
    path = tmp_path / "unused.db"
    with pytest.raises(ValueError):
        preview_preparation(path, articles, configurations)
    assert not path.exists()


@pytest.mark.parametrize("target", ["configuration", "destination", "prompt"])
def test_preview_refuses_inactive_configuration_context(corpus, monkeypatch, target):
    path, repository = corpus
    configuration = repository.catalog()["configurations"][0]
    context = repository.configuration_context(configuration["id"])
    context[target]["archived" if target == "prompt" else "enabled"] = target == "prompt"
    monkeypatch.setattr(GenerationRepository, "configuration_context", lambda self, identifier: context)
    with pytest.raises(ValueError):
        preview_preparation(path, [1], [configuration["id"]])


def test_execution_is_unconditionally_disabled(monkeypatch):
    monkeypatch.setenv("GENERATION_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-private-test-value")
    with pytest.raises(GenerationDisabledError, match="Generación deshabilitada: solo preparación local"):
        execute_generation(provider="openai", article_ids=[1], configuration_ids=[1])


def test_public_status_is_only_presence_and_never_exposes_keys_or_paths(tmp_path, monkeypatch):
    env_path = tmp_path / ".env.testing"
    env_path.write_text("OPENAI_API_KEY='fake-file-value' # comment\nGEMINI_API_KEY=\nIGNORED=hidden\nGENERATION_ENABLED=true\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GENERATION_ENABLED", "true")
    assert public_status(env_path) == {
        "mode": "preparation", "execution_enabled": False,
        "credentials": [{"provider": "openai", "configured": True}, {"provider": "gemini", "configured": False}],
    }
    monkeypatch.setenv("OPENAI_API_KEY", " ")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-env-value")
    assert public_status(env_path)["credentials"] == [
        {"provider": "openai", "configured": False}, {"provider": "gemini", "configured": True},
    ]


def test_env_parser_is_literal_and_handles_absent_or_malformed_files(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    missing = tmp_path / ".env.missing"
    assert not any(row["configured"] for row in public_status(missing)["credentials"])
    env_path = tmp_path / ".env.testing"
    env_path.write_text('export OPENAI_API_KEY="$(dangerous_command) ${MISSING}"\nGEMINI_API_KEY="unterminated\n', encoding="utf-8-sig")
    assert public_status(env_path)["credentials"] == [
        {"provider": "openai", "configured": True}, {"provider": "gemini", "configured": False},
    ]
