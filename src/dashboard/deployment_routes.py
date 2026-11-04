"""Local WordPress preparation endpoints, protected by the panel CSRF middleware."""
from flask import Blueprint, jsonify, request

from src.deployment.preparation import preview_draft
from src.deployment.wp_publisher import publish_draft


def register_deployment_routes(app, repository):
    routes = Blueprint('deployment', __name__)

    def payload():
        if not request.is_json:
            raise ValueError('La solicitud debe contener JSON.')
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('La solicitud debe ser un objeto JSON.')
        return body

    @routes.get('/api/deployment/catalog')
    def catalog():
        return jsonify(repository.catalog())

    @routes.put('/api/deployment/targets/<int:target_id>')
    def save_target(target_id):
        return jsonify(repository.save_target(target_id, payload()))

    @routes.post('/api/deployment/preview')
    def preview():
        return jsonify(preview_draft(repository, payload()))

    @routes.post('/api/deployment/execute')
    def execute():
        publish_draft()

    app.register_blueprint(routes)
