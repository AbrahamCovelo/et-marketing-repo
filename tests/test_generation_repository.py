"""Editorial preparation is versioned, atomic, and never a generated output."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3

import pytest

from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.repository import GenerationRepository


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "articles.db"
    articles = []
    with Database(path) as db:
        for index in range(2):
            url = f"https://movingtospain.com/article-{index}/"
            result = ExtractionResult(url=url, canonical_url=url, title=f"Source {index}",
                published_at="2026-01-01T00:00:00Z", modified_at="2026-02-01T00:00:00Z",
                original_text="Useful paragraph. Removed attribution.",
                blocks=[{"key": "b1", "position": 0, "source_order": 1, "kind": "paragraph",
                         "text": "Useful paragraph.", "section_path": [], "data": {}, "flags": []}])
            db.store_article(result, raw_html="<main>Useful paragraph.</main>")
            articles.append(result)
    return path, GenerationRepository(path), articles


def prompt_payload(prompt, **changes):
    return {**{key: prompt[key] for key in ("name", "description", "system_prompt", "template", "status", "archived")}, **changes}


def config_payload(config, **changes):
    return {**{key: config[key] for key in ("name", "destination_id", "prompt_version_id", "provider", "model", "notes", "enabled")}, **changes}


def destination_payload(destination, **changes):
    return {**{key: destination[key] for key in ("name", "site_url", "locale", "notes", "enabled")}, **changes}


def test_model_is_pinned_per_preparation_without_editing_configuration(corpus):
    _, repo, _ = corpus
    original = repo.catalog()
    first = repo.create_plan([1], [1, 2, 3], model_override="gpt-6-luna")
    assert {item["model"] for item in first["items"]} == {"gpt-6-luna"}
    assert all(item["configuration"]["model"] == "" for item in first["items"])
    assert not any("Falta elegir un modelo" in warning for item in first["items"] for warning in item["warnings"])
    assert repo.catalog() == original
    duplicate = repo.create_plan([1], [1, 2, 3], model_override="gpt-6-luna")
    assert duplicate["created_count"] == 0 and duplicate["reused_count"] == 3
    different = repo.create_plan([1], [1], model_override="gpt-6.1-terra")
    assert different["created_count"] == 1
    assert repo.article_history(1)["prepared_count"] == 4
    config = original["configurations"][0]
    repo.save_configuration(config_payload(config, model="gpt-6-astra"), config["id"])
    old = repo.plan_detail(first["id"])["items"][0]
    assert old["model"] == "gpt-6-luna" and old["configuration_changed"]
    assert repo.article_history(1)["generated_count"] == 0


def test_upgrade_reads_legacy_models_and_reuses_legacy_preparations(corpus):
    path, repo, _ = corpus
    config = repo.catalog()["configurations"][0]
    repo.save_configuration(config_payload(config, model="gpt-6-luna"), config["id"])
    first = repo.create_plan([1], [1])
    with Database(path) as db, db.connection:
        db.connection.execute("ALTER TABLE generation_preparations DROP COLUMN generation_options_json")
    upgraded = GenerationRepository(path)
    legacy = upgraded.plan_detail(first["id"])["items"][0]
    assert legacy["model"] == "gpt-6-luna" and legacy["provider"] == "openai"
    again = upgraded.create_plan([1], [1], model_override="gpt-6-luna")
    assert again["created_count"] == 0 and again["reused_count"] == 1
    assert again["items"][0]["id"] == legacy["id"]


def test_supplied_templates_import_preserves_history_and_ctas(corpus):
    from src.generation.presets import load_editorial_prompts
    from src.generation.preparation import preview_preparation
    path, repo, _ = corpus
    previous = repo.create_plan([1], [1, 2, 3])
    loaded = load_editorial_prompts(path)
    assert {item["locale"] for item in loaded} == {"en-CA", "fr-CA", "es-CO"}
    catalog = repo.catalog()
    assert all(p["version"] == 2 and p["status"] == "draft" for p in catalog["prompts"])
    expected = {"en-CA": ("CTA_IMMIGRATION_EN", "CTA_TAXES_EN"), "fr-CA": ("CTA_IMMIGRATION_FR", "CTA_TAXES_FR"), "es-CO": ("CTA_INMIGRACION_ES", "CTA_IMPUESTOS_ES")}
    for config in catalog["configurations"]:
        prompt = repo.configuration_context(config["id"])["prompt"]
        assert prompt["system_prompt"] and "{{article_text}}" in prompt["template"]
        assert "{source_text}" not in prompt["template"]
        assert all(f"[{cta}]" in prompt["template"] for cta in expected[config["locale"]])
        assert "<h2>" in prompt["template"] and "<strong>" in prompt["template"]
        preview = preview_preparation(path, [1], [config["id"]], "gpt-6-luna")
        assert "Useful paragraph." in preview["example"]["rendered_prompt"]
        assert "{{article_text}}" not in preview["example"]["rendered_prompt"]
    assert all(item["prompt"]["template"] == "" for item in repo.plan_detail(previous["id"])["items"])
    assert load_editorial_prompts(path) == loaded
    assert repo.catalog() == catalog
    assert repo.article_history(1)["generated_count"] == 0


def test_seed_is_idempotent_empty_and_preserves_source_schema_and_corpus(corpus):
    path, repo, _ = corpus
    initial = repo.catalog()
    again = GenerationRepository(path).catalog()
    assert initial == again
    assert {row["locale"] for row in initial["destinations"]} == {"en-CA", "fr-CA", "es-CO"}
    assert len(initial["destinations"]) == len(initial["prompts"]) == len(initial["configurations"]) == 3
    assert all(prompt["status"] == "draft" and prompt["template"] == "" and prompt["system_prompt"] == ""
               for prompt in initial["prompts"])
    assert all(not config["model"] and config["provider"] == "openai" for config in initial["configurations"])
    with Database(path) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.stats()["articles"] == 2
        assert db.stats()["versions"] == 2


def test_seed_failure_rolls_back_extension_schema_and_preserves_core(tmp_path, monkeypatch):
    path = tmp_path / "articles.db"
    with Database(path) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 1
    original_seed = GenerationRepository._seed

    def fail_after_insert(self, connection):
        original_seed(self, connection)
        raise RuntimeError("Simulated initialization failure")

    monkeypatch.setattr(GenerationRepository, "_seed", fail_after_insert)
    with pytest.raises(RuntimeError, match="initialization"):
        GenerationRepository(path)
    with Database(path) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'generation_%'").fetchall() == []
    monkeypatch.setattr(GenerationRepository, "_seed", original_seed)
    assert len(GenerationRepository(path).catalog()["destinations"]) == 3


def test_edit_prompt_keeps_configuration_pinned_until_explicit_change(corpus):
    _, repo, _ = corpus
    catalog = repo.catalog()
    original = catalog["prompts"][0]
    config = catalog["configurations"][0]
    edited = repo.save_prompt(prompt_payload(original, system_prompt="Editorial directions.",
                            template="{{article_json}}", status="reviewed"), original["id"])
    assert edited["version"] == 2
    assert edited["version_id"] != original["version_id"]
    context = repo.configuration_context(config["id"])
    assert context["prompt"]["version_id"] == original["version_id"]
    assert context["prompt"]["template"] == ""
    assert context["configuration"]["newer_prompt_available"] is True
    assert context["configuration"]["warnings"]
    saved = repo.save_configuration(config_payload(config, prompt_version_id=edited["version_id"]), config["id"])
    assert saved["current_version"] == 2
    assert repo.configuration_context(config["id"])["prompt"]["template"] == "{{article_json}}"
    assert repo.catalog()["prompts"][0]["versions"][1]["id"] == original["version_id"]


def test_noop_save_does_not_create_versions_and_seeds_do_not_restore_edited_values(corpus):
    path, repo, _ = corpus
    catalog = repo.catalog()
    prompt = catalog["prompts"][0]
    config = catalog["configurations"][0]
    assert repo.save_prompt(prompt_payload(prompt), prompt["id"])["version_id"] == prompt["version_id"]
    assert repo.save_configuration(config_payload(config), config["id"])["current_version_id"] == config["current_version_id"]
    destination = catalog["destinations"][0]
    repo.save_destination(destination_payload(destination, name="Updated destination"), destination["id"])
    assert GenerationRepository(path).catalog()["destinations"][0]["name"] == "Updated destination"


@pytest.mark.parametrize("changes", [
    {"status": "reviewed"}, {"status": "complete"}, {"template": "{{__import__('os')}}"},
    {"template": "{{article_text.__class__}}"}, {"template": "{{unknown}}"},
    {"system_prompt": "{{unclosed"}, {"template": "{unopened}}"}, {"archived": "false"},
    {"name": ""}, {"template": []},
])
def test_prompt_validation_rejects_invalid_templates_without_partial_write(corpus, changes):
    _, repo, _ = corpus
    before = repo.catalog()
    with pytest.raises(ValueError):
        repo.save_prompt(prompt_payload(before["prompts"][0], **changes), 1)
    assert repo.catalog() == before


def test_reviewed_prompt_requires_real_source_placeholder(corpus):
    _, repo, _ = corpus
    prompt = repo.catalog()["prompts"][0]
    with pytest.raises(ValueError):
        repo.save_prompt(prompt_payload(prompt, status="reviewed", system_prompt="Instructions",
                         template="{{article_title}} for {{destination_name}}"), 1)
    saved = repo.save_prompt(prompt_payload(prompt, status="reviewed", system_prompt="Instructions",
                            template="{{ article_text }} for {{locale}}"), 1)
    assert saved["status"] == "reviewed"


def test_nested_json_is_allowed_and_unsupported_execution_fields_are_rejected(corpus):
    _, repo, _ = corpus
    catalog = repo.catalog()
    prompt = catalog["prompts"][0]
    template = '{{article_text}}\nReturn {"article":{"title":"text"}}'
    assert repo.save_prompt(prompt_payload(prompt, template=template), 1)["template"] == template
    for saver, payload in ((repo.save_prompt, prompt_payload(prompt)),
                           (repo.save_destination, destination_payload(catalog["destinations"][0])),
                           (repo.save_configuration, config_payload(catalog["configurations"][0]))):
        with pytest.raises(ValueError, match="campos no admitidos"):
            saver({**payload, "execution_enabled": True})


def test_configuration_rejects_unknown_foreign_keys_and_invalid_provider(corpus):
    _, repo, _ = corpus
    original = repo.catalog()
    config = original["configurations"][0]
    for changes in ({"destination_id": 999}, {"prompt_version_id": 999}, {"provider": "https://other.test"},
                    {"destination_id": True}, {"enabled": "true"}):
        with pytest.raises((ValueError, KeyError)):
            repo.save_configuration(config_payload(config, **changes))
    assert repo.catalog() == original


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///secret", "https://name:secret@example.com", "https://"])
def test_destination_rejects_unsafe_url(corpus, url):
    _, repo, _ = corpus
    destination = repo.catalog()["destinations"][0]
    with pytest.raises(ValueError):
        repo.save_destination(destination_payload(destination, site_url=url), destination["id"])


def test_plan_crossproduct_is_atomic_and_pins_complete_audit_context(corpus):
    path, repo, _ = corpus
    with pytest.raises(KeyError):
        repo.create_plan([1, 2], [1, 999])
    with pytest.raises(KeyError):
        repo.create_plan([1, 999], [1, 2])
    assert repo.list_plans() == []
    plan = repo.create_plan([1, 2], [1, 2, 3])
    assert plan["created_count"] == 6 and plan["reused_count"] == 0
    assert plan["status"] == "prepared"
    assert plan["generated_count"] == 0 and plan["execution_enabled"] is False
    assert {(item["article_id"], item["configuration_id"]) for item in plan["items"]} == {
        (article_id, config_id) for article_id in (1, 2) for config_id in (1, 2, 3)}
    item = plan["items"][0]
    assert item["source"]["published_at"] == "2026-01-01T00:00:00Z"
    assert item["source_version_id"] == item["source"]["version_id"]
    assert item["prompt_version_id"] == item["prompt"]["version_id"]
    assert item["configuration_version_id"] == item["configuration"]["current_version_id"]
    assert item["destination"]["locale"] == "en-CA"
    with Database(path) as db:
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_duplicate_plans_reuse_preparations_and_cancel_without_affecting_other_batches(corpus):
    _, repo, _ = corpus
    first = repo.create_plan([1], [1])
    repeated = repo.create_plan([1, 1], [1, 1])
    assert repeated["created_count"] == 0 and repeated["reused_count"] == 1
    assert first["items"][0]["id"] == repeated["items"][0]["id"]
    assert repo.article_history(1)["prepared_count"] == 1
    cancelled = repo.cancel_plan(repeated["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["items"][0]["status"] == "cancelled"
    assert cancelled["items"][0]["preparation_status"] == "prepared"
    assert repo.plan_detail(first["id"])["items"][0]["status"] == "prepared"
    assert repo.article_history(1)["prepared_count"] == 1
    repo.cancel_plan(first["id"])
    assert repo.article_history(1)["prepared_count"] == 0
    later = repo.create_plan([1], [1])
    assert later["created_count"] == 1
    assert later["items"][0]["id"] != first["items"][0]["id"]


def test_history_is_truthful_and_preserves_snapshots_after_all_inputs_change(corpus):
    path, repo, articles = corpus
    before = repo.create_plan([1], [1])["items"][0]
    catalog = repo.catalog()
    prompt = repo.save_prompt(prompt_payload(catalog["prompts"][0], template="{{article_text}}"), 1)
    # Merely creating a prompt revision must not upgrade a pinned configuration or duplicate its preparation.
    assert repo.create_plan([1], [1])["created_count"] == 0
    repo.save_configuration(config_payload(catalog["configurations"][0], name="New configuration",
                                           prompt_version_id=prompt["version_id"]), 1)
    repo.save_destination(destination_payload(catalog["destinations"][0], name="New destination"), 1)
    changed = deepcopy(articles[0])
    changed.blocks[0]["text"] = "Changed factual paragraph."
    with Database(path) as db:
        db.store_article(changed, raw_html="updated source")
    history = repo.article_history(1)
    item = history["items"][0]
    assert history["generated_count"] == 0 and history["adaptation_status"] == "not_adapted"
    assert history["message"] == "Todavía no hubo adaptaciones."
    assert item["source_changed"] and item["configuration_changed"] and item["destination_changed"]
    assert item["newer_prompt_available"]
    assert item["source"] == before["source"] and item["prompt"] == before["prompt"]
    assert item["configuration_name"] == before["configuration_name"]
    assert item["destination_name"] == before["destination_name"]
    assert repo.create_plan([1], [1])["created_count"] == 1
    summaries = repo.article_summaries([1, 2])
    assert summaries[1]["generated_count"] == summaries[2]["generated_count"] == 0
    assert summaries[1]["prepared_count"] == 2 and summaries[2]["prepared_count"] == 0
    assert repo.article_summaries([]) == {}


def test_disabled_settings_cannot_be_prepared_and_do_not_change_history(corpus):
    _, repo, _ = corpus
    catalog = repo.catalog()
    repo.save_destination(destination_payload(catalog["destinations"][0], enabled=False), 1)
    repo.save_configuration(config_payload(catalog["configurations"][1], enabled=False), 2)
    repo.save_prompt(prompt_payload(catalog["prompts"][2], archived=True), 3)
    for config_id in (1, 2, 3):
        with pytest.raises(ValueError):
            repo.create_plan([1], [config_id])
    assert repo.list_plans() == []
    assert repo.article_history(1)["prepared_count"] == 0


def test_versions_cannot_be_overwritten_by_sql(corpus):
    path, repo, _ = corpus
    with Database(path) as db:
        for statement in ("UPDATE generation_prompt_versions SET template='modified' WHERE id=1",
                          "DELETE FROM generation_prompt_versions WHERE id=1",
                          "UPDATE generation_configuration_versions SET model='modified' WHERE id=1"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.connection.execute(statement)
    assert repo.configuration_context(1)["prompt"]["template"] == ""


def test_simultaneous_double_click_cannot_create_duplicate_preparations(corpus):
    _, repo, _ = corpus
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repo.create_plan([1, 2], [1, 2]), range(2)))
    assert sorted(result["created_count"] for result in results) == [0, 4]
    assert sorted(result["reused_count"] for result in results) == [0, 4]
    assert repo.article_history(1)["prepared_count"] == 2


@pytest.mark.parametrize("article_ids,config_ids", [([], [1]), ([1], []), ([True], [1]), ([1], ["1"]),
    ([1], [2**63]), ("1", [1]), ([1] * 501, [1]), (list(range(1, 252)), [1, 2])])
def test_plan_rejects_invalid_and_oversized_requests(corpus, article_ids, config_ids):
    _, repo, _ = corpus
    with pytest.raises(ValueError):
        repo.create_plan(article_ids, config_ids)
    assert repo.list_plans() == []


def test_sql_text_is_literal_and_ids_are_not_interpolated(corpus):
    path, repo, _ = corpus
    prompt = repo.catalog()["prompts"][0]
    name = "'); DROP TABLE source_articles; --"
    saved = repo.save_prompt(prompt_payload(prompt, name=name), 1)
    assert saved["name"] == name
    with pytest.raises(ValueError):
        repo.plan_detail("1 OR 1=1")
    with pytest.raises(ValueError):
        repo.article_summaries(["1) OR 1=1 --"])
    with Database(path) as db:
        assert db.stats()["articles"] == 2


def test_schema_never_accepts_completed_or_generated_preparation(corpus):
    path, repo, _ = corpus
    plan = repo.create_plan([1], [1])
    with Database(path) as db:
        for table in ("generation_plans", "generation_preparations"):
            with pytest.raises(sqlite3.IntegrityError):
                db.connection.execute(f"UPDATE {table} SET status='completed' WHERE id=?", (plan["id"],))
    assert repo.article_history(1)["generated_count"] == 0
