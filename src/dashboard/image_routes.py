"""Explicit external CC0 searches and confirmed media imports from the panel."""
import re

from flask import Blueprint, current_app, jsonify, request

from src.deployment.images import CATALOGUES, get_import_record, import_candidate, search_candidates, suggest_queries
from src.deployment.repository import _id


def register_image_routes(app):
    routes = Blueprint("images", __name__)

    def payload(fields, optional=()):
        if not request.is_json:
            raise ValueError("La solicitud debe contener JSON.")
        body = request.get_json()
        if not isinstance(body, dict) or not fields <= set(body) or set(body) - fields - set(optional):
            raise ValueError("Los campos de la solicitud de imágenes no son válidos.")
        return body

    def query_ids(fields):
        if set(request.args) != set(fields) or any(len(request.args.getlist(field)) != 1 for field in fields):
            raise ValueError("Los identificadores de la consulta no son válidos.")
        result = []
        for field in fields:
            value = request.args[field]
            if not re.fullmatch(r"[1-9][0-9]{0,18}", value):
                raise ValueError("Los identificadores deben ser enteros positivos.")
            result.append(_id(int(value)))
        return result

    @routes.post("/api/deployment/images/search")
    def search():
        body = payload({"query", "provider"})
        if not isinstance(body["provider"], str) or body["provider"] not in CATALOGUES:
            raise ValueError("Seleccione WordPress Photos u Openverse CC0.")
        query = body["query"]
        if not isinstance(query, str) or not query.strip() or len(query) > 150 or any(ord(c) < 32 for c in query):
            raise ValueError("Escriba una búsqueda de entre 1 y 150 caracteres.")
        return jsonify(search_candidates(current_app.config["DATABASE_PATH"], query.strip(), body["provider"]))

    @routes.post("/api/deployment/images/import")
    def import_image():
        body = payload({"target_id", "candidate_id", "confirm"}, {"retry_failed"})
        if body["confirm"] is not True:
            raise ValueError("Confirme la importación de la fotografía al WordPress seleccionado.")
        _id(body["target_id"])
        _id(body["candidate_id"])
        options = {}
        if "retry_failed" in body:
            if type(body["retry_failed"]) is not bool:
                raise ValueError("La confirmación de reintento debe ser booleana.")
            options["retry_failed"] = body["retry_failed"]
        return jsonify(import_candidate(current_app.config["DATABASE_PATH"], body["target_id"], body["candidate_id"],
                                        env_path=current_app.config["DEPLOYMENT_ENV_PATH"], **options))

    @routes.get("/api/deployment/images/import")
    def import_status():
        target_id, candidate_id = query_ids(("target_id", "candidate_id"))
        return jsonify(record=get_import_record(current_app.config["DATABASE_PATH"], target_id, candidate_id))

    @routes.get("/api/deployment/images/suggestions")
    def suggestions():
        article_id, = query_ids(("article_id",))
        detail = current_app.extensions["panel_repository"].detail(article_id)
        article = detail["article"]
        return jsonify(article_id=article_id, article_title=article["title"],
                       queries=suggest_queries(article["title"], article.get("metadata", {}).get("categories", [])))

    app.register_blueprint(routes)
