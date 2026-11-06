"""The only initial generation entry point in the panel; invoked explicitly."""
from flask import Blueprint, jsonify, request

from .generation_jobs import GenerationJobManager
from .generation_budget import create_quote
from src.generation.usage import audit_usage


def register_execution_routes(app):
    manager = GenerationJobManager(app.config["DATABASE_PATH"], env_path=app.config["GENERATION_ENV_PATH"],
        cache_path=app.config.get("MODEL_CATALOG_CACHE_PATH"))
    app.extensions["generation_jobs"] = manager
    routes = Blueprint("generation_execution", __name__)

    @routes.get('/api/generation/usage')
    def usage():
        return jsonify(audit_usage(app.config['DATABASE_PATH']))

    @routes.post('/api/generation/cost-preview')
    def cost_preview():
        if not request.is_json:
            raise ValueError('La solicitud debe contener JSON.')
        return jsonify(create_quote(app.config['DATABASE_PATH'], request.get_json()))

    @routes.get("/api/generation/runs")
    def list_runs():
        return jsonify(items=manager.list())

    @routes.get("/api/generation/runs/<int:run_id>")
    def get_run(run_id):
        return jsonify(manager.get(run_id))

    @routes.post("/api/generation/runs")
    def start_run():
        if not request.is_json:
            raise ValueError("La solicitud debe contener JSON.")
        if not isinstance(request.get_json(), dict) or not request.get_json().get('budget_quote_id'):
            raise ValueError('Revise y acepte la estimación de coste antes de generar.')
        return jsonify(manager.submit(request.get_json())), 202

    @routes.post("/api/generation/runs/<int:run_id>/resume")
    def resume_saved(run_id):
        if not request.is_json or request.get_json() != {}:
            raise ValueError("La recuperación usa los resultados existentes y requiere un objeto JSON vacío.")
        return jsonify(manager.resume_saved(run_id)), 202

    app.register_blueprint(routes)
