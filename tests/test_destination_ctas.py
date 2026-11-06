"""Destination CTA editing shares the WordPress mapping without remote changes."""
import sqlite3

import pytest
import requests

from src.dashboard.app import create_app
from src.deployment.content import prepare_wordpress_content
from src.deployment.repository import CTA_NAMES, DeploymentRepository


@pytest.fixture
def panel(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("CTA configuration must remain local")
    monkeypatch.setattr(requests.Session, "request", no_network)
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    path = tmp_path / "ctas.db"
    app = create_app(path, test_config={"TESTING": True, "GENERATION_ENV_PATH": str(env),
                                       "DEPLOYMENT_ENV_PATH": str(env)})
    return app.test_client(), {"X-Panel-Token": app.config["PANEL_TOKEN"]}, path


def update(panel, destination_id, links, **extra):
    client, headers, _ = panel
    return client.put(f"/api/generation/destinations/{destination_id}/ctas",
                      json={"cta_links": links, **extra}, headers=headers)


@pytest.mark.parametrize("destination_id,locale", [(1, "en-CA"), (2, "fr-CA"), (3, "es-CO")])
def test_existing_destinations_expose_both_empty_ctas_without_credentials(panel, destination_id, locale):
    client, _, _ = panel
    result = client.get(f"/api/generation/destinations/{destination_id}/ctas")
    assert result.status_code == 200
    assert result.json["editable"] is True
    assert result.json["locale"] == locale
    assert result.json["cta_links"] == {name: {"url": "", "text": ""} for name in CTA_NAMES[locale]}
    assert "credentials" not in result.json
    assert update(panel, destination_id, result.json["cta_links"]).status_code == 200


def test_destination_ctas_are_the_same_mapping_used_for_wordpress(panel):
    client, _, path = panel
    original = client.get("/api/deployment/catalog").json["targets"][0]
    data = client.get("/api/generation/destinations/1/ctas").json
    links = data["cta_links"]
    links["CTA_IMMIGRATION_EN"] = {"url": "https://canadiansinspain.com/immigration/",
                                    "text": "Immigration <help> & advice"}
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in (
            "generation_destinations", "generation_configuration_versions", "generation_prompt_versions")}
    saved = update(panel, 1, links)
    assert saved.status_code == 200
    target = DeploymentRepository(path).get_target(original["id"])
    assert target["cta_links"] == links
    for key in ("guide_url", "category_ids", "language_mode", "notes"):
        assert target[key] == original[key]
    result = prepare_wordpress_content("<h2>Moving</h2><p>Read [CTA_IMMIGRATION_EN].</p>", target["cta_links"])
    assert result["ready"]
    assert '<a href="https://canadiansinspain.com/immigration/">Immigration &lt;help&gt; &amp; advice</a>' in result["content"]
    pending = prepare_wordpress_content("<p>Read [CTA_TAXES_EN].</p>", target["cta_links"])
    assert not pending["ready"] and pending["content"] is None
    with sqlite3.connect(path) as db:
        assert {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in before} == before
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("bad_link", [
    {"url": "javascript:alert(1)", "text": "Go"},
    {"url": "https://user:secret@canadiansinspain.com/path", "text": "Go"},
    {"url": "https://different.example/path", "text": "Go"},
    {"url": "https://canadiansinspain.com/path", "text": "x" * 301},
    {"url": "https://canadiansinspain.com/path", "text": "Bad\x00text"},
    {"url": "https://canadiansinspain.com/path", "text": "Go", "html": "<script>"},
    {"url": "https://canadiansinspain.com/path"},
])
def test_invalid_cta_update_is_atomic(panel, bad_link):
    client, _, _ = panel
    before = client.get("/api/generation/destinations/1/ctas").json
    links = {**before["cta_links"], "CTA_IMMIGRATION_EN": bad_link}
    assert update(panel, 1, links).status_code == 400
    assert client.get("/api/generation/destinations/1/ctas").json == before


def test_foreign_markers_unknown_fields_and_missing_csrf_are_rejected(panel):
    client, _, _ = panel
    original = client.get("/api/generation/destinations/1/ctas").json
    assert client.put("/api/generation/destinations/1/ctas", json={"cta_links": original["cta_links"]}).status_code == 403
    assert update(panel, 1, original["cta_links"], guide_url="https://example.test").status_code == 400
    assert update(panel, 1, {"CTA_IMMIGRATION_FR": {"url": "", "text": ""}}).status_code == 400
    assert update(panel, 1, {}).status_code == 400
    assert client.get("/api/generation/destinations/999/ctas").status_code == 404


def test_partial_pending_values_can_be_saved_and_cleared(panel):
    client, _, _ = panel
    links = client.get("/api/generation/destinations/3/ctas").json["cta_links"]
    links["CTA_INMIGRACION_ES"]["text"] = "Asesoría migratoria"
    assert update(panel, 3, links).status_code == 200
    assert client.get("/api/generation/destinations/3/ctas").json["cta_links"] == links
    links["CTA_INMIGRACION_ES"]["text"] = ""
    assert update(panel, 3, links).status_code == 200


def test_destination_without_connection_is_explicitly_pending(panel):
    client, headers, _ = panel
    created = client.post("/api/generation/destinations", headers=headers, json={
        "name": "Future destination", "site_url": "https://future.example", "locale": "en-CA",
        "notes": "", "enabled": True}).json
    result = client.get(f"/api/generation/destinations/{created['id']}/ctas").json
    assert not result["editable"] and result["blockers"] and result["target_id"] is None
    assert len(result["cta_links"]) == 2
    assert update(panel, created["id"], result["cta_links"]).status_code == 400


def test_mismatched_destination_cannot_edit_other_language_links(panel):
    client, _, path = panel
    with sqlite3.connect(path) as db:
        db.execute("UPDATE generation_destinations SET locale='fr-CA' WHERE id=1")
    result = client.get("/api/generation/destinations/1/ctas").json
    assert not result["editable"] and result["blockers"]
    assert update(panel, 1, result["cta_links"]).status_code == 400
