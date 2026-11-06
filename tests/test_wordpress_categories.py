"""Real policy/selection code exercised with free, fake WordPress GET responses."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from src.dashboard.app import create_app
from src.deployment.categories import (category_context, get_category_catalog,
    refresh_category_catalog, save_category_policy, validate_category_selection)
from src.deployment.repository import DeploymentRepository
from src.deployment.trial_categories import get_trial_categories, save_trial_categories
from src.deployment.management import delete_adaptation, preview_redeployment, redeploy_adaptation
from src.deployment.trial import get_deployment_trial, send_trial_draft
from test_deployment_trial import saved_trial, Session, send
from test_adaptation_management import Remote


def term(identifier, name=None, **kwargs):
    return {"id": identifier, "name": name or f"Topic {identifier}", "slug": f"topic-{identifier}",
            "parent": 0, "description": "", **kwargs}


class Taxonomy:
    def __init__(self, pages=None, default=91, settings_status=200, page_headers=True, before_response=None):
        self.pages = pages if pages is not None else [[term(12), term(42), term(91, "Default renamed")]]
        self.default, self.settings_status, self.page_headers = default, settings_status, page_headers
        self.calls = []
        self.before_response = before_response

    def request(self, method, url, **kwargs):
        assert method == "GET"
        assert url == "https://colombianosespana.com/"
        assert kwargs["allow_redirects"] is False
        self.calls.append((method, url, kwargs))
        params = kwargs["params"]
        if self.before_response:
            self.before_response(params)
        if params["rest_route"].endswith("settings"):
            return SimpleNamespace(status_code=self.settings_status, headers={}, json=lambda: {"default_category": self.default})
        page = self.pages[params["page"] - 1]
        headers = {"X-WP-TotalPages": str(len(self.pages))} if self.page_headers else {}
        return SimpleNamespace(status_code=200, headers=headers, json=lambda: page)


@pytest.fixture
def taxonomy_db(tmp_path, monkeypatch):
    path, env = tmp_path / "categories.db", tmp_path / ".env"
    env.write_text('WP_COLOMBIANS_ES_USERNAME="test"\nWP_COLOMBIANS_ES_APPLICATION_PASSWORD="private-test-password"', encoding="utf-8")
    monkeypatch.delenv("WP_COLOMBIANS_ES_USERNAME", raising=False)
    monkeypatch.delenv("WP_COLOMBIANS_ES_APPLICATION_PASSWORD", raising=False)
    DeploymentRepository(path, env)
    return path, env


def configure(taxonomy_db, *, session=None):
    path, env = taxonomy_db
    catalog = refresh_category_catalog(path, 3, env, session or Taxonomy())
    return save_category_policy(path, 3, {"allowed_category_ids": [12, 42],
        "excluded_default_category_id": 91, "catalog_revision": catalog["catalog_revision"]})


def test_cached_get_does_not_create_tables_or_contact_wordpress(taxonomy_db, monkeypatch):
    path, _ = taxonomy_db
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("unexpected HTTP"))
    before = path.read_bytes()
    result = get_category_catalog(path, 3)
    assert result["policy"] is None and not result["configured"] and result["stale"]
    assert path.read_bytes() == before


def test_full_live_catalog_is_dynamic_and_excludes_actual_default(taxonomy_db):
    path, env = taxonomy_db
    # The actual default deliberately differs from WordPress' installation ID.
    client = Taxonomy([[term(12, "Retirement &amp; taxes"), term(42)], [term(91)]])
    catalog = configure(taxonomy_db, session=client)
    assert [call[2]["params"].get("page") for call in client.calls] == [1, 2, None]
    assert catalog["configured"] and catalog["wordpress_default_category_id"] == 91
    assert catalog["categories"][0]["name"] == "Retirement & taxes"
    ctx = category_context(path, 3, env, cached_only=True)
    assert [row["id"] for row in ctx["candidates"]] == [12, 42]
    assert "private-test-password" not in json.dumps(catalog)
    assert b"private-test-password" not in path.read_bytes()


def test_settings_denied_requires_explicit_excluded_default_and_whitelist(taxonomy_db):
    path, env = taxonomy_db
    catalog = refresh_category_catalog(path, 3, env, Taxonomy(settings_status=403))
    assert catalog["wordpress_default_category_id"] is None and catalog["default_status"] == "unavailable"
    with pytest.raises(ValueError, match="Confirme"):
        category_context(path, 3, cached_only=True)
    with pytest.raises(ValueError, match="requieren"):
        save_category_policy(path, 3, {"allowed_category_ids": [12], "catalog_revision": catalog["catalog_revision"]})
    configured = save_category_policy(path, 3, {"allowed_category_ids": [12], "excluded_default_category_id": 91,
                                               "catalog_revision": catalog["catalog_revision"]})
    assert configured["configured"]


@pytest.mark.parametrize("overrides", [
    {"allowed_category_ids": []}, {"allowed_category_ids": [12, 91]},
    {"allowed_category_ids": [999]}, {"allowed_category_ids": [True]},
    {"excluded_default_category_id": 42}, {"catalog_revision": "outdated"},
])
def test_policy_rejects_empty_default_unknown_and_outdated_choices(taxonomy_db, overrides):
    path, env = taxonomy_db
    catalog = refresh_category_catalog(path, 3, env, Taxonomy())
    body = {"allowed_category_ids": [12], "excluded_default_category_id": 91,
            "catalog_revision": catalog["catalog_revision"], **overrides}
    with pytest.raises(ValueError):
        save_category_policy(path, 3, body)
    assert get_category_catalog(path, 3)["policy"] is None


@pytest.mark.parametrize("pages", [
    [[term(12)], [term(12)]], [[term(12)], []],
    [[term(True)]], [[term(12, name="") | {"name": ""}]],
])
def test_incomplete_or_malformed_catalog_preserves_previous_cache(taxonomy_db, pages):
    path, env = taxonomy_db
    before = configure(taxonomy_db)
    with pytest.raises(ValueError):
        refresh_category_catalog(path, 3, env, Taxonomy(pages))
    assert get_category_catalog(path, 3) == before


def test_missing_pagination_header_never_accepts_truncated_catalog(taxonomy_db):
    path, env = taxonomy_db
    with pytest.raises(ValueError, match="paginación"):
        refresh_category_catalog(path, 3, env, Taxonomy([[term(i) for i in range(1, 101)]], page_headers=False))


def test_cached_only_context_never_refreshes_stale_cache(taxonomy_db, monkeypatch):
    path, _ = taxonomy_db
    configure(taxonomy_db)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE wordpress_category_catalogs SET fetched_at='2020-01-01T00:00:00+00:00'")
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("unexpected HTTP"))
    assert len(category_context(path, 3, cached_only=True)["candidates"]) == 2


def test_selected_terms_are_revalidated_before_delivery(taxonomy_db):
    path, env = taxonomy_db
    configure(taxonomy_db)
    ctx = category_context(path, 3, cached_only=True)
    assert validate_category_selection(path, 3, [12], context=ctx, env_path=env, session=Taxonomy())["category_ids"] == [12]
    changed = Taxonomy([[term(12, "Different subject"), term(42), term(91)]])
    with pytest.raises(ValueError, match="cambió"):
        validate_category_selection(path, 3, [12], context=ctx, env_path=env, session=changed)
    with pytest.raises(ValueError):
        validate_category_selection(path, 3, [91], env_path=env, session=Taxonomy())
    with pytest.raises(ValueError):
        validate_category_selection(path, 3, [], env_path=env, session=Taxonomy())


def test_unrelated_new_term_does_not_break_existing_selection(taxonomy_db):
    path, env = taxonomy_db
    configure(taxonomy_db)
    ctx = category_context(path, 3, cached_only=True)
    client = Taxonomy([[term(12), term(42), term(73, "New category"), term(91)]])
    result = validate_category_selection(path, 3, [42], context=ctx, env_path=env, session=client)
    assert result["category_ids"] == [42]
    assert len(category_context(path, 3, cached_only=True)["candidates"]) == 2


def test_category_override_is_local_and_applies_to_legacy_trial(saved_trial, monkeypatch):
    path, env = saved_trial
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("unexpected HTTP"))
    detail = get_trial_categories(path, 1)
    assert detail["category_ids"] == [] and detail["source"] == "pending"
    saved = save_trial_categories(path, 1, {"category_ids": [23], "catalog_revision": detail["catalog"]["catalog_revision"]})
    assert saved["category_ids"] == [23] and saved["source"] == "manual"
    session = Session(post_body={"id": 1510, "status": "draft", "featured_media": 1369, "categories": [23]})
    result = send(saved_trial, session)
    assert result["payload"]["categories"] == [23]
    assert result["status"] == "draft"


def test_sender_blocks_legacy_empty_without_wordpress_writes(saved_trial):
    path, env = saved_trial
    session = Session()
    with pytest.raises(ValueError, match="al menos"):
        send_trial_draft(path, 1, title="Retirement", featured_media=1369, category_ids=[], env_path=env, session=session)
    assert not any(call[0] == "POST" for call in session.calls)
    assert get_deployment_trial(path, 1) is None


def test_sender_uses_saved_model_selection_without_another_api_call(saved_trial):
    path, env = saved_trial
    selection = {**category_context(path, 3, cached_only=True), "selected_category_ids": [23], "source": "model"}
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE generation_assessments (id INTEGER PRIMARY KEY, article_id INTEGER,
            configuration_id INTEGER,status TEXT,category_selection_json TEXT)""")
        db.execute("INSERT INTO generation_assessments VALUES (11,1,3,'completed',?)", (json.dumps(selection),))
        db.execute("UPDATE generation_trials SET snapshot_json=? WHERE id=1",
                   (json.dumps({"applicability_review": {"assessment_id": 11}}),))
    session = Session(post_body={"id": 1510, "status": "draft", "featured_media": 1369, "categories": [23]})
    result = send_trial_draft(path, 1, title="Saved assessment", featured_media=1369,
                              category_ids=[], env_path=env, session=session)
    assert result["status"] == "draft" and result["payload"]["categories"] == [23]
    assert get_trial_categories(path, 1)["source"] == "model"
    assert all(call[1] == "https://colombianosespana.com/" for call in session.calls)


def test_sender_does_not_claim_success_if_wordpress_ignores_categories(saved_trial):
    session = Session(post_body={"id": 1510, "status": "draft", "featured_media": 1369, "categories": [77]})
    result = send(saved_trial, session)
    assert result["status"] == "unknown" and "categorías" in result["error"]
    assert send(saved_trial, session) == result
    assert len([call for call in session.calls if call[0] == "POST"]) == 1


def test_selection_changed_during_preflight_cannot_be_sent(saved_trial):
    path, _ = saved_trial
    class ChangedSelection(Session):
        def request(self, method, url, **kwargs):
            if method == "GET" and "/media/" in kwargs["params"]["rest_route"]:
                catalog = get_category_catalog(path, 3)
                save_trial_categories(path, 1, {"category_ids": [23], "catalog_revision": catalog["catalog_revision"]})
            return super().request(method, url, **kwargs)
    session = ChangedSelection()
    with pytest.raises(ValueError, match="selección de categorías cambió"):
        send(saved_trial, session)
    assert not any(call[0] == "POST" for call in session.calls)
    assert get_deployment_trial(path, 1) is None


def test_redeploy_preserves_legitimate_remote_categories_over_empty_archive(saved_trial):
    path, env = saved_trial
    send(saved_trial, Session())
    with sqlite3.connect(path) as db:
        payload = json.loads(db.execute("SELECT payload_json FROM deployment_trials").fetchone()[0])
        payload["categories"] = []
        db.execute("UPDATE deployment_trials SET payload_json=?", (json.dumps(payload),))
    remote = Remote()
    remote.remote["categories"] = [23]
    remote.post_body["categories"] = [23]
    preview = preview_redeployment(path, 1, env_path=env, session=remote)
    assert preview["categories"] == [23]
    result = redeploy_adaptation(path, 1, request_key="preserve-remote-category", revision=preview["revision"], env_path=env, session=remote)
    assert result["status"] == "completed"
    assert [call for call in remote.calls if call[0] == "POST"][0][2]["json"]["categories"] == [23]


def test_legacy_default_stops_until_explicit_local_correction(saved_trial):
    path, env = saved_trial
    send(saved_trial, Session())
    remote = Remote()
    remote.remote["categories"] = [77]
    with pytest.raises(ValueError, match="Guarde las categorías"):
        preview_redeployment(path, 1, env_path=env, session=remote)
    detail = get_trial_categories(path, 1)
    save_trial_categories(path, 1, {"category_ids": [19], "catalog_revision": detail["catalog"]["catalog_revision"]})
    assert preview_redeployment(path, 1, env_path=env, session=remote)["categories"] == [19]
    assert not any(call[0] == "POST" for call in remote.calls)


def test_delete_also_removes_category_override(saved_trial):
    path, _ = saved_trial
    detail = get_trial_categories(path, 1)
    save_trial_categories(path, 1, {"category_ids": [19], "catalog_revision": detail["catalog"]["catalog_revision"]})
    delete_adaptation(path, 1)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM adaptation_category_overrides").fetchone()[0] == 0


def test_panel_category_routes_are_local_and_csrf_protected(saved_trial, monkeypatch):
    path, env = saved_trial
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("unexpected HTTP"))
    app = create_app(path, test_config={"TESTING": True, "DEPLOYMENT_ENV_PATH": str(env)})
    if "wordpress_categories" not in app.blueprints:
        from src.dashboard.category_routes import register_category_routes
        register_category_routes(app)
    client = app.test_client()
    catalog = client.get("/api/deployment/targets/3/categories").json
    body = {"category_ids": [19], "catalog_revision": catalog["catalog_revision"]}
    assert client.put("/api/generation/trials/1/categories", json=body).status_code == 403
    response = client.put("/api/generation/trials/1/categories", json=body,
                          headers={"X-Panel-Token": app.config["PANEL_TOKEN"]})
    assert response.status_code == 200 and response.json["category_ids"] == [19]
    assert client.get("/api/generation/trials/1/categories").json["source"] == "manual"
