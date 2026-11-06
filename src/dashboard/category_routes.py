"""Local taxonomy policy endpoints; only refresh performs free WordPress GETs."""
from flask import Blueprint, current_app, jsonify, request

from src.deployment.categories import get_category_catalog, refresh_category_catalog, save_category_policy
from src.deployment.trial_categories import get_trial_categories, save_trial_categories


def register_category_routes(app):
    routes = Blueprint("wordpress_categories", __name__)

    def payload():
        if not request.is_json:
            raise ValueError("La solicitud debe contener JSON.")
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("La solicitud debe ser un objeto JSON.")
        return body

    @routes.get("/api/deployment/targets/<int:target_id>/categories")
    def catalog(target_id):
        return jsonify(get_category_catalog(current_app.config["DATABASE_PATH"], target_id))

    @routes.post("/api/deployment/targets/<int:target_id>/categories/refresh")
    def refresh(target_id):
        if payload():
            raise ValueError("La actualización no acepta parámetros adicionales.")
        return jsonify(refresh_category_catalog(current_app.config["DATABASE_PATH"], target_id,
                                                current_app.config.get("DEPLOYMENT_ENV_PATH")))

    @routes.put("/api/deployment/targets/<int:target_id>/categories/policy")
    def save_policy(target_id):
        return jsonify(save_category_policy(current_app.config["DATABASE_PATH"], target_id, payload()))

    @routes.get("/api/generation/trials/<int:trial_id>/categories")
    def trial_categories(trial_id):
        return jsonify(get_trial_categories(current_app.config["DATABASE_PATH"], trial_id))

    @routes.put("/api/generation/trials/<int:trial_id>/categories")
    def save_trial_selection(trial_id):
        return jsonify(save_trial_categories(current_app.config["DATABASE_PATH"], trial_id, payload()))

    app.register_blueprint(routes)
