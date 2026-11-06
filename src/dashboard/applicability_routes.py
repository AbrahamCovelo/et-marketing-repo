"""Local editorial reviews; these endpoints never call a model or fetch evidence."""
import re

from flask import Blueprint, current_app, jsonify, request

from src.generation.applicability import review_status, save_review


def register_applicability_routes(app):
    routes = Blueprint('applicability', __name__)

    @routes.get('/api/generation/applicability')
    def get_review():
        fields = {'article_id', 'configuration_id'}
        if set(request.args) != fields or any(len(request.args.getlist(key)) != 1 for key in fields):
            raise ValueError('Seleccione un artículo y una configuración para consultar la revisión.')
        values = []
        for key in ('article_id', 'configuration_id'):
            value = request.args[key]
            if not re.fullmatch(r'[1-9][0-9]{0,18}', value) or int(value) > 2**63 - 1:
                raise ValueError('El artículo y la configuración deben tener identificadores válidos.')
            values.append(int(value))
        return jsonify(review_status(current_app.config['DATABASE_PATH'], *values))

    @routes.post('/api/generation/applicability')
    def create_review():
        if not request.is_json:
            raise ValueError('La revisión debe enviarse como JSON.')
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {'article_id', 'configuration_id', 'context_fingerprint', 'review'}:
            raise ValueError('Se requieren el artículo, la configuración, el contexto consultado y la revisión.')
        for key in ('article_id', 'configuration_id'):
            if type(body[key]) is not int or not 0 < body[key] <= 2**63 - 1:
                raise ValueError('El artículo y la configuración deben tener identificadores válidos.')
        fingerprint = body['context_fingerprint']
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
            raise ValueError('Consulte el artículo y su configuración antes de guardar una revisión.')
        db_path = current_app.config['DATABASE_PATH']
        saved = save_review(db_path, body['article_id'], body['configuration_id'], body['review'],
                            expected_context_fingerprint=fingerprint)
        return jsonify(review=saved, status=review_status(db_path, body['article_id'], body['configuration_id'])), 201

    app.register_blueprint(routes)
