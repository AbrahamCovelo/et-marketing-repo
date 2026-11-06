"""Local editorial edits. Saving never sends to a provider or WordPress."""
from flask import Blueprint, current_app, jsonify, request

from src.deployment.management import save_adaptation_content


def register_adaptation_routes(app, repository):
    routes = Blueprint('adaptation_editor', __name__)

    @routes.put('/api/generation/trials/<int:trial_id>/content')
    def save_content(trial_id):
        if not request.is_json:
            raise ValueError('La solicitud debe contener JSON.')
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {'html', 'revision'}:
            raise ValueError('Se requieren html y revision para guardar la adaptación.')
        save_adaptation_content(current_app.config['DATABASE_PATH'], trial_id, **body)
        return jsonify(repository.trial_detail(trial_id))

    app.register_blueprint(routes)
