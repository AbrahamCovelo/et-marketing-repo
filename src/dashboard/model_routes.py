"""Free, read-only discovery of account models and documented capabilities."""
from flask import Blueprint, current_app, jsonify, request

from src.generation.model_catalog import get_catalog, get_model_capabilities


def register_model_routes(app):
    routes = Blueprint("model_catalog", __name__)

    def options():
        return {"env_path": current_app.config.get("GENERATION_ENV_PATH"),
                "cache_path": current_app.config.get("MODEL_CATALOG_CACHE_PATH")}

    @routes.get("/api/generation/models")
    def models():
        return jsonify(get_catalog(**options()))

    @routes.post("/api/generation/models/refresh")
    def refresh_models():
        if not request.is_json or request.get_json() != {}:
            raise ValueError("La actualización requiere un objeto JSON vacío.")
        return jsonify(get_catalog(**options(), refresh=True))

    @routes.get("/api/generation/models/<model_id>")
    def model_capabilities(model_id):
        return jsonify(get_model_capabilities(model_id, cache_path=options()["cache_path"]))

    app.register_blueprint(routes)
