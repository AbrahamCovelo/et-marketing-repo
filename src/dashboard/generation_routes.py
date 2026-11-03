"""Preparation-only generation routes. No route dispatches work to an AI service."""
from flask import Blueprint, current_app, jsonify, request

from src.generation.preparation import PLACEHOLDERS, execute_generation, preview_preparation
from src.generation.settings import public_status


def register_generation_routes(app, repository):
    routes = Blueprint('generation', __name__)

    def payload():
        if not request.is_json:
            raise ValueError('La solicitud debe contener JSON.')
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('La solicitud debe ser un objeto JSON.')
        return body

    def selection_payload():
        body = payload()
        if set(body) != {'article_ids', 'configuration_ids'}:
            raise ValueError('Se requieren article_ids y configuration_ids.')
        return body['article_ids'], body['configuration_ids']

    @routes.get('/api/generation/catalog')
    def catalog():
        return jsonify(
            **repository.catalog(),
            **public_status(env_path=current_app.config['GENERATION_ENV_PATH']),
            placeholders=PLACEHOLDERS,
        )

    @routes.post('/api/generation/destinations')
    def create_destination():
        return jsonify(repository.save_destination(payload())), 201

    @routes.put('/api/generation/destinations/<int:destination_id>')
    def update_destination(destination_id):
        return jsonify(repository.save_destination(payload(), destination_id=destination_id))

    @routes.post('/api/generation/prompts')
    def create_prompt():
        return jsonify(repository.save_prompt(payload())), 201

    @routes.put('/api/generation/prompts/<int:prompt_id>')
    def update_prompt(prompt_id):
        return jsonify(repository.save_prompt(payload(), prompt_id=prompt_id))

    @routes.post('/api/generation/configurations')
    def create_configuration():
        return jsonify(repository.save_configuration(payload())), 201

    @routes.get('/api/generation/configurations/<int:configuration_id>')
    def configuration_detail(configuration_id):
        return jsonify(repository.configuration_context(configuration_id))

    @routes.put('/api/generation/configurations/<int:configuration_id>')
    def update_configuration(configuration_id):
        return jsonify(repository.save_configuration(payload(), configuration_id=configuration_id))

    @routes.post('/api/generation/preview')
    def preview():
        article_ids, configuration_ids = selection_payload()
        return jsonify(preview_preparation(current_app.config['DATABASE_PATH'], article_ids, configuration_ids))

    @routes.post('/api/generation/plans')
    def prepare_plan():
        article_ids, configuration_ids = selection_payload()
        return jsonify(repository.create_plan(article_ids, configuration_ids)), 201

    @routes.get('/api/generation/plans')
    def plans():
        try:
            limit = int(request.args.get('limit', '30'))
        except (ValueError, TypeError):
            raise ValueError('El límite debe ser un número entero.') from None
        return jsonify(items=repository.list_plans(limit=limit))

    @routes.get('/api/generation/plans/<int:plan_id>')
    def plan_detail(plan_id):
        return jsonify(repository.plan_detail(plan_id))

    @routes.post('/api/generation/plans/<int:plan_id>/cancel')
    def cancel_plan(plan_id):
        body = payload()
        if body:
            raise ValueError('La cancelación no requiere parámetros.')
        return jsonify(repository.cancel_plan(plan_id))

    @routes.get('/api/articles/<int:article_id>/adaptations')
    def article_adaptations(article_id):
        return jsonify(repository.article_history(article_id))

    @routes.post('/api/generation/execute')
    def execute_disabled():
        # Unconditionally blocked, even if a caller bypasses the disabled button,
        # supplies a valid key, or sets environment variables to request execution.
        execute_generation()

    app.register_blueprint(routes)
