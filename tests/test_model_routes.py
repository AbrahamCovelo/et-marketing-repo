"""HTTP contract for free discovery, isolated from the live account."""
from flask import Flask

from src.dashboard.model_routes import register_model_routes


def panel(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, GENERATION_ENV_PATH="private-env", MODEL_CATALOG_CACHE_PATH="temporary-cache")
    calls = []

    def catalog(**kwargs):
        calls.append(("catalog", kwargs))
        return {"models": [{"id": "gpt-new-example"}], "status": "available", "stale": False}

    def capabilities(model_id, **kwargs):
        calls.append(("capabilities", model_id, kwargs))
        return {"id": model_id, "reasoning_efforts": ["low", "high"], "automatic_supported": True}

    monkeypatch.setattr("src.dashboard.model_routes.get_catalog", catalog)
    monkeypatch.setattr("src.dashboard.model_routes.get_model_capabilities", capabilities)
    register_model_routes(app)

    @app.errorhandler(ValueError)
    def invalid(error):
        return {"error": str(error)}, 400

    return app.test_client(), calls


def test_dynamic_list_route_uses_private_configuration(monkeypatch):
    client, calls = panel(monkeypatch)
    response = client.get("/api/generation/models")
    assert response.status_code == 200 and response.json["models"][0]["id"] == "gpt-new-example"
    assert calls == [("catalog", {"env_path": "private-env", "cache_path": "temporary-cache"})]
    assert "private-env" not in response.text


def test_explicit_refresh_forces_fresh_list(monkeypatch):
    client, calls = panel(monkeypatch)
    response = client.post("/api/generation/models/refresh", json={})
    assert response.status_code == 200
    assert calls[0][1]["refresh"] is True


def test_refresh_rejects_arbitrary_provider_fields(monkeypatch):
    client, calls = panel(monkeypatch)
    assert client.post("/api/generation/models/refresh", json={"api_key": "do-not-use"}).status_code == 400
    assert calls == []


def test_selected_model_returns_capabilities(monkeypatch):
    client, calls = panel(monkeypatch)
    response = client.get("/api/generation/models/gpt-99-new")
    assert response.status_code == 200
    assert response.json["reasoning_efforts"] == ["low", "high"]
    assert calls == [("capabilities", "gpt-99-new", {"cache_path": "temporary-cache"})]
