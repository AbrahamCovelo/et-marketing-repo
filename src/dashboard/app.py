"""Local HTTP interface for inspecting the corpus and running extraction jobs."""
from __future__ import annotations

import hmac
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from src.extraction.db_manager import Database
from src.generation.repository import GenerationRepository
from src.generation.preparation import GenerationDisabledError
from .generation_routes import register_generation_routes
from .jobs import JobBusyError, JobManager
from .repository import Repository
from src.deployment.repository import DeploymentRepository
from src.deployment.wp_publisher import DeploymentDisabledError
from .deployment_routes import register_deployment_routes
from src.deployment.management import initialize_management, AdaptationConflict


def _integer(value, name: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} debe ser un número entero.') from None


def _boolean(value) -> bool:
    if value in (None, '', 'false', '0'):
        return False
    if value in ('true', '1'):
        return True
    raise ValueError('selected_only debe ser true o false.')


def create_app(db_path='data/source_articles.db', *, job_manager=None, test_config=None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE_PATH=str(Path(db_path).resolve()),
        PANEL_TOKEN=secrets.token_urlsafe(32),
        MAX_CONTENT_LENGTH=512 * 1024,
        TRUSTED_HOSTS=['127.0.0.1', 'localhost', '[::1]'],
        GENERATION_ENV_PATH=str(Path(__file__).resolve().parents[2] / '.env'),
        DEPLOYMENT_ENV_PATH=str(Path(__file__).resolve().parents[2] / '.env'),
    )
    if test_config:
        app.config.update(test_config)
    app.json.ensure_ascii = False
    repository = Repository(app.config['DATABASE_PATH'])
    generation = GenerationRepository(app.config['DATABASE_PATH'])
    deployment = DeploymentRepository(app.config['DATABASE_PATH'], app.config['DEPLOYMENT_ENV_PATH'])
    initialize_management(app.config['DATABASE_PATH'])
    jobs = job_manager if job_manager is not None else JobManager(app.config['DATABASE_PATH'])
    app.extensions['panel_repository'] = repository
    app.extensions['panel_jobs'] = jobs
    app.extensions['generation_repository'] = generation
    app.extensions['deployment_repository'] = deployment

    @app.before_request
    def protect_local_actions():
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            token = request.headers.get('X-Panel-Token', '')
            if not hmac.compare_digest(token, app.config['PANEL_TOKEN']):
                return jsonify(error='La sesión del panel cambió. Recargue la página.'), 403
            origin = request.headers.get('Origin')
            if origin:
                source = urlsplit(origin)
                current = urlsplit(request.host_url)
                if (source.scheme, source.netloc) != (current.scheme, current.netloc):
                    return jsonify(error='Esta acción debe ejecutarse desde el panel local.'), 403

    @app.after_request
    def local_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
        )
        return response

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(AdaptationConflict)
    def adaptation_conflict(error):
        return jsonify(error=str(error)), 409

    @app.errorhandler(KeyError)
    def not_found(error):
        return jsonify(error='No se encontró el artículo, la configuración o la operación solicitada.'), 404

    @app.errorhandler(GenerationDisabledError)
    def generation_disabled(error):
        return jsonify(error=str(error), mode='preparation', execution_enabled=False), 409

    @app.errorhandler(DeploymentDisabledError)
    def deployment_disabled(error):
        return jsonify(error=str(error), mode='preparation', execution_enabled=False), 409

    @app.errorhandler(JobBusyError)
    def operation_busy(error):
        return jsonify(error=str(error)), 409

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def server_error(error):
        app.logger.exception('Error al atender una petición del panel')
        return jsonify(error='No se pudo completar la operación. Consulte el registro de la consola.'), 500

    def payload():
        if not request.is_json:
            raise ValueError('La solicitud debe contener JSON.')
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('La solicitud debe ser un objeto JSON.')
        return body

    @app.get('/')
    def index():
        return render_template('index.html', panel_token=app.config['PANEL_TOKEN'])

    @app.get('/api/summary')
    def summary():
        return jsonify(repository.summary())

    @app.get('/api/articles')
    def article_list():
        data = repository.list_articles(
            q=request.args.get('q', ''),
            date_field=request.args.get('date_field', 'published_at'),
            date_from=request.args.get('date_from') or None,
            date_to=request.args.get('date_to') or None,
            review=request.args.get('review', 'all'),
            selected_only=_boolean(request.args.get('selected_only')),
            page=_integer(request.args.get('page'), 'page', 1),
            page_size=_integer(request.args.get('page_size'), 'page_size', 25),
            sort=request.args.get('sort', 'published_desc'),
        )
        histories = generation.article_summaries([item['id'] for item in data['items']])
        for item in data['items']:
            item.update(histories[item['id']])
        return jsonify(data)

    @app.get('/api/articles/<int:article_id>')
    def article_detail(article_id):
        detail = repository.detail(article_id)
        detail['adaptations'] = generation.article_history(article_id)
        return jsonify(detail)

    @app.get('/api/articles/<int:article_id>/export')
    def article_export(article_id):
        with Database(app.config['DATABASE_PATH']) as database:
            data = database.get_export(article_id=article_id)
        return Response(app.json.dumps(data, indent=2) + '\n', mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename="article-{article_id}.json"'})

    @app.post('/api/selection')
    def selection():
        data = payload()
        if set(data) != {'article_ids', 'selected'}:
            raise ValueError('Se requieren article_ids y selected.')
        return jsonify(repository.set_selection(data['article_ids'], data['selected']))

    @app.post('/api/selection/matching')
    def select_matching():
        return jsonify(repository.select_matching(**payload()))

    @app.get('/api/runs')
    def runs():
        return jsonify(items=repository.recent_runs(limit=_integer(request.args.get('limit'), 'limit', 20)))

    @app.get('/api/jobs')
    def job_list():
        return jsonify(items=jobs.list_jobs())

    @app.get('/api/jobs/<job_id>')
    def job_detail(job_id):
        return jsonify(jobs.get_job(job_id))

    @app.post('/api/jobs')
    def start_job():
        return jsonify(jobs.submit(payload())), 202

    register_generation_routes(app, generation)
    register_deployment_routes(app, deployment)
    return app


def run(db_path='data/source_articles.db', *, port=8765, open_browser=False):
    if not 1 <= port <= 65535:
        raise ValueError('El puerto debe estar entre 1 y 65535.')
    app = create_app(db_path)
    if open_browser:
        timer = threading.Timer(1, webbrowser.open, args=(f'http://127.0.0.1:{port}/',))
        timer.daemon = True
        timer.start()
    # One local process: the reloader would duplicate the background-job manager.
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)
    return 0
