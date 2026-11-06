"""Image routes use panel CSRF and require explicit, narrowly scoped imports."""
import pytest
import requests

from src.dashboard.app import create_app
from src.dashboard import image_routes


@pytest.fixture
def panel(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Image API tests must never contact remote services")
    monkeypatch.setattr(requests.Session, "request", no_network)
    app = create_app(tmp_path / "articles.db", test_config={"TESTING": True,
        "DEPLOYMENT_ENV_PATH": str(tmp_path / "absent.env"), "GENERATION_ENV_PATH": str(tmp_path / "absent.env")})
    if "images" not in app.blueprints:
        image_routes.register_image_routes(app)
    return app, app.test_client()


def post(panel, route, body):
    app, client = panel
    return client.post(route, json=body, headers={"X-Panel-Token": app.config["PANEL_TOKEN"]})


def test_search_calls_only_catalogue_service(panel, monkeypatch):
    calls = []
    result = {"provider": "wordpress_photos", "query": "Barcelona", "candidates": [],
              "status": "needs_image", "warnings": [], "selected_candidate_id": None}
    monkeypatch.setattr(image_routes, "search_candidates", lambda *args: calls.append(args) or result)
    response = post(panel, "/api/deployment/images/search", {"query": " Barcelona ", "provider": "wordpress_photos"})
    assert response.status_code == 200 and response.json == result
    assert calls == [(panel[0].config["DATABASE_PATH"], "Barcelona", "wordpress_photos")]


def test_import_is_explicit_and_scoped_to_saved_candidate(panel, monkeypatch):
    calls = []
    record = {"status": "ready", "wp_media_id": 1234, "target_id": 3, "candidate_id": 2}
    monkeypatch.setattr(image_routes, "import_candidate", lambda *args, **kwargs: calls.append((args, kwargs)) or record)
    response = post(panel, "/api/deployment/images/import", {"target_id": 3, "candidate_id": 2, "confirm": True})
    assert response.status_code == 200 and response.json == record
    assert calls == [((panel[0].config["DATABASE_PATH"], 3, 2), {"env_path": panel[0].config["DEPLOYMENT_ENV_PATH"]})]


def test_failed_import_requires_explicit_retry_flag(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(image_routes, "import_candidate", lambda *args, **kwargs: calls.append(kwargs) or {"status": "ready"})
    body = {"target_id": 3, "candidate_id": 2, "confirm": True, "retry_failed": True}
    assert post(panel, "/api/deployment/images/import", body).status_code == 200
    assert calls[0]["retry_failed"] is True
    assert post(panel, "/api/deployment/images/import", {**body, "retry_failed": "true"}).status_code == 400
    assert len(calls) == 1


@pytest.mark.parametrize("body", [
    {"target_id": 3, "candidate_id": 2}, {"target_id": 3, "candidate_id": 2, "confirm": False},
    {"target_id": 3, "candidate_id": 2, "confirm": 1}, {"target_id": True, "candidate_id": 2, "confirm": True},
    {"target_id": 3, "candidate_id": -1, "confirm": True},
    {"target_id": 3, "candidate_id": 2, "confirm": True, "url": "https://other.test/a.jpg"},
])
def test_import_invalid_or_unconfirmed_payload_never_dispatches(panel, monkeypatch, body):
    monkeypatch.setattr(image_routes, "import_candidate", lambda *args, **kwargs: pytest.fail("Must not import"))
    assert post(panel, "/api/deployment/images/import", body).status_code == 400


@pytest.mark.parametrize("body", [
    {"query": "Barcelona", "provider": "arbitrary"}, {"query": "", "provider": "openverse"},
    {"query": "x" * 151, "provider": "openverse"}, {"query": "a\nb", "provider": "openverse"},
    {"query": "Barcelona", "provider": []}, {"query": "Barcelona", "provider": "openverse", "url": "https://other.test"},
])
def test_search_rejects_invalid_queries_and_arbitrary_provider_urls(panel, monkeypatch, body):
    monkeypatch.setattr(image_routes, "search_candidates", lambda *args: pytest.fail("Must not search"))
    assert post(panel, "/api/deployment/images/search", body).status_code == 400


def test_post_actions_require_panel_token_and_same_origin(panel, monkeypatch):
    app, client = panel
    monkeypatch.setattr(image_routes, "search_candidates", lambda *args: pytest.fail("Must not search"))
    body = {"query": "Barcelona", "provider": "wordpress_photos"}
    assert client.post("/api/deployment/images/search", json=body).status_code == 403
    assert client.post("/api/deployment/images/import", json={}).status_code == 403
    assert client.post("/api/deployment/images/search", json=body,
        headers={"X-Panel-Token": app.config["PANEL_TOKEN"], "Origin": "https://other.test"}).status_code == 403
    assert client.get("/api/deployment/images/search").status_code == 405


def test_service_error_is_returned_without_attempting_import(panel, monkeypatch):
    def unavailable(*args):
        raise ValueError("El catálogo respondió HTTP 504.")
    monkeypatch.setattr(image_routes, "search_candidates", unavailable)
    response = post(panel, "/api/deployment/images/search", {"query": "Barcelona", "provider": "openverse"})
    assert response.status_code == 400 and response.json["error"] == "El catálogo respondió HTTP 504."


def test_unknown_import_is_reported_as_record_not_retried(panel, monkeypatch):
    calls = []
    record = {"status": "unknown", "error": "Requiere comprobación manual, sin reintento."}
    monkeypatch.setattr(image_routes, "import_candidate", lambda *args, **kwargs: calls.append(args) or record)
    response = post(panel, "/api/deployment/images/import", {"target_id": 3, "candidate_id": 2, "confirm": True})
    assert response.json == record and len(calls) == 1


def test_read_import_status_never_creates_or_imports(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(image_routes, "get_import_record", lambda *args: calls.append(args) or None)
    response = panel[1].get("/api/deployment/images/import?target_id=3&candidate_id=2")
    assert response.status_code == 200 and response.json == {"record": None}
    assert calls == [(panel[0].config["DATABASE_PATH"], 3, 2)]
    assert panel[1].get("/api/deployment/images/import?target_id=3&candidate_id=2&candidate_id=3").status_code == 400


def test_suggestions_use_saved_title_without_network(panel, monkeypatch):
    repository = panel[0].extensions["panel_repository"]
    monkeypatch.setattr(repository, "detail", lambda article_id: {"article": {
        "title": "Moving to Barcelona: visas and schools", "metadata": {"categories": []}}})
    result = panel[1].get("/api/deployment/images/suggestions?article_id=1")
    assert result.status_code == 200
    assert result.json["queries"][0] == "Barcelona"
    assert result.json["article_id"] == 1
    assert panel[1].get("/api/deployment/images/suggestions?article_id=-1").status_code == 400


def test_unknown_article_is_not_found(panel):
    assert panel[1].get("/api/deployment/images/suggestions?article_id=999").status_code == 404
