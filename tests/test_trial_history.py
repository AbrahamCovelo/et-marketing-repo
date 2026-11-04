"""Read-only trial history is separate from preparations and provider payloads."""
import json
import sqlite3

import pytest
import requests

from src.dashboard.app import create_app
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.trial import _SCHEMA
from src.deployment.trial import _SCHEMA as DEPLOYMENT_SCHEMA


@pytest.fixture
def trial_panel(tmp_path, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("Reading trial history cannot contact providers")

    monkeypatch.setattr(requests.Session, "request", reject_network)
    path = tmp_path / "articles.db"
    with Database(path) as db:
        for index in range(2):
            url = f"https://movingtospain.com/example-{index}/"
            db.store_article(ExtractionResult(
                url=url, canonical_url=url, title=f"Article {index}",
                blocks=[{"key": "b1", "position": 0, "source_order": 1, "kind": "paragraph",
                         "text": "Source text.", "data": {}, "flags": []}],
            ), raw_html="<p>Source text.</p>")
    app = create_app(path, test_config={"TESTING": True})
    return path, app, app.test_client(), app.extensions["generation_repository"]


def add_trials(path):
    # Fixture for the persisted trial contract; no execution or provider request.
    with sqlite3.connect(path) as connection:
        connection.execute(_SCHEMA)
        for trial_id, status in enumerate(("completed", "incomplete", "failed", "unknown", "running"), 1):
            connection.execute("""INSERT INTO generation_trials
                (id,run_key,article_id,source_version_id,configuration_id,configuration_version_id,
                prompt_version_id,destination_id,model,status,created_at,completed_at,
                usage_json,html,request_json,response_json,snapshot_json) VALUES
                (?,?,1,1,3,3,3,3,'gpt-5-nano-2025-08-07',?,'2026-11-04T12:00:00Z',
                '2026-11-04T12:01:00Z',?,?,?,?,?)""", (
                trial_id, f"fixture-{trial_id}", status,
                json.dumps({"input_tokens": 100, "output_tokens": 200, "total_tokens": 300,
                            "unexpected": "private-response-data"}),
                '<h2>Barcelona</h2><p>Texto de prueba.</p><script>alert("unsafe")</script>',
                '{"secret":"private-request-data"}', '{"secret":"private-response-data"}',
                '{"secret":"private-snapshot-data"}',
            ))


def test_existing_database_without_trials_stays_readable(trial_panel):
    _, _, client, repository = trial_panel
    assert repository.article_history(1)["trials"] == []
    assert repository.article_summaries([1])[1]["generated_count"] == 0
    assert client.get("/api/generation/trials/1").status_code == 404


def test_completed_trial_counts_once_and_preparations_remain_separate(trial_panel):
    path, _, client, repository = trial_panel
    add_trials(path)
    repository.create_plan([1], [3])
    history = client.get("/api/articles/1/adaptations").json
    assert history["generated_count"] == 1
    assert history["prepared_count"] == 1
    assert history["adaptation_status"] == "adapted"
    assert "pendientes de revisión editorial" in history["message"]
    assert len(history["trials"]) == 5
    assert all("html" not in trial for trial in history["trials"])
    assert next(trial for trial in history["trials"] if trial["id"] == 1)["review_status"] == "pending"
    summaries = repository.article_summaries([1, 2])
    assert summaries[1]["generated_count"] == 1
    assert summaries[2]["generated_count"] == 0
    assert summaries[1]["configurations"] == [{"id": 3, "name": "Colombians in Spain (Español)", "version": 1}]
    listed = next(item for item in client.get("/api/articles").json["items"] if item["id"] == 1)
    assert listed["generated_count"] == 1
    assert client.get("/api/articles/1").json["adaptations"]["generated_count"] == 1


def test_failed_and_incomplete_results_never_mark_article_adapted(trial_panel):
    path, _, client, repository = trial_panel
    add_trials(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM generation_trials WHERE status='completed'")
    assert repository.article_summaries([1])[1]["adaptation_status"] == "not_adapted"
    history = client.get("/api/articles/1/adaptations").json
    assert history["generated_count"] == 0
    assert history["message"] == "Todavía no hubo adaptaciones."
    assert all(trial["review_status"] == "not_ready" for trial in history["trials"])


def test_trial_endpoint_exposes_only_safe_result_and_bulk_execution_stays_blocked(trial_panel):
    path, app, client, _ = trial_panel
    add_trials(path)
    response = client.get("/api/generation/trials/1")
    assert response.status_code == 200
    assert response.content_type == "application/json"
    assert response.json["html"].startswith("<h2>Barcelona")
    assert response.json["usage"] == {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300}
    assert not {"request", "response", "snapshot", "request_json", "response_json", "snapshot_json"} & response.json.keys()
    assert "private-" not in response.get_data(as_text=True)
    assert client.get("/api/generation/trials/999").status_code == 404
    assert client.post("/api/generation/execute", json={},
                       headers={"X-Panel-Token": app.config["PANEL_TOKEN"]}).status_code == 409


def add_deployment(path, *, status="draft", post_id=123, target_id=3, post_status="draft"):
    with sqlite3.connect(path) as connection:
        connection.execute(DEPLOYMENT_SCHEMA)
        connection.execute("""INSERT INTO deployment_trials
            (generation_trial_id,target_id,status,payload_json,warnings_json,result_json,error,created_at,updated_at)
            VALUES (1,?,?,?, ?,?,'private-error-data','2026-11-04T12:00:00Z','2026-11-04T12:01:00Z')""", (
                target_id, status, '{"secret":"private-payload-data"}', '["private-warning-data"]',
                json.dumps({"post_id": post_id, "post_status": post_status,
                            "edit_url": "https://attacker.example/collect?secret=private-secret",
                            "link": "javascript:alert(1)", "extra": "private-result-data"}),
            ))


def test_draft_history_builds_safe_editor_link_without_private_delivery_data(trial_panel):
    path, _, client, repository = trial_panel
    add_trials(path)
    add_deployment(path)
    # A mutable destination URL and the provider response cannot control the editor host.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE generation_destinations SET site_url='https://attacker.example' WHERE id=3")
    response = client.get("/api/generation/trials/1")
    deployment = response.json["deployment"]
    assert deployment == {
        "status": "draft", "updated_at": "2026-11-04T12:01:00Z", "post_id": 123,
        "editor_url": "https://colombianosespana.com/wp-admin/post.php?post=123&action=edit",
    }
    assert "private-" not in response.get_data(as_text=True)
    assert "attacker" not in response.get_data(as_text=True)
    assert "javascript:" not in response.get_data(as_text=True)
    trial = next(t for t in repository.article_history(1)["trials"] if t["id"] == 1)
    assert trial["deployment"] == deployment
    assert trial["review_status"] == "pending"


@pytest.mark.parametrize("overrides", [
    {"post_id": -1}, {"post_id": 0}, {"post_id": True}, {"post_id": "123"},
    {"post_id": 2**63}, {"status": "unknown"}, {"status": "failed"},
    {"status": "sending"}, {"post_status": "publish"},
])
def test_only_confirmed_draft_with_positive_numeric_id_gets_editor_link(trial_panel, overrides):
    path, _, client, _ = trial_panel
    add_trials(path)
    add_deployment(path, **overrides)
    deployment = client.get("/api/generation/trials/1").json["deployment"]
    assert deployment["editor_url"] is None
    assert deployment["post_id"] is None


def test_mismatched_target_and_unknown_connection_cannot_produce_editor_link(trial_panel):
    path, _, client, _ = trial_panel
    add_trials(path)
    add_deployment(path, target_id=1)
    assert client.get("/api/generation/trials/1").json["deployment"] is None
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE deployment_trials SET target_id=3")
        connection.execute("UPDATE deployment_targets SET connection_key='https://attacker.example' WHERE id=3")
    assert client.get("/api/generation/trials/1").json["deployment"]["editor_url"] is None
