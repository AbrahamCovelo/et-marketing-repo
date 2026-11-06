"""Explicit panel actions on saved adaptations; never calls a text model."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import time

import requests

from src.generation.trial import get_trial
from src.generation.content_edits import effective_content
from .content import prepare_wordpress_content, validate_semantic_html
from .repository import CTA_NAMES, DeploymentRepository, _id
from .settings import load_credentials
from .trial import get_deployment_trial, _same_origin
from .categories import validate_category_selection
from .trial_categories import assert_category_state, trial_category_selection


class AdaptationConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def initialize_management(db_path):
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS adaptation_edits (
            id INTEGER PRIMARY KEY, trial_id INTEGER NOT NULL REFERENCES generation_trials(id),
            version INTEGER NOT NULL CHECK(version>0), html TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(trial_id,version))""")
        db.execute("""CREATE TABLE IF NOT EXISTS adaptation_deletions (
            trial_id INTEGER PRIMARY KEY REFERENCES generation_trials(id), deleted_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS adaptation_actions (
            id INTEGER PRIMARY KEY, trial_id INTEGER NOT NULL REFERENCES generation_trials(id),
            request_key TEXT NOT NULL UNIQUE, revision TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('running','completed','failed','unknown')),
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_json)),
            error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS adaptation_active_action
            ON adaptation_actions(trial_id) WHERE status IN ('running','unknown')""")


def _assert_idle(db, trial_id):
    if db.execute("SELECT 1 FROM adaptation_actions WHERE trial_id=? AND status IN ('running','unknown')", (trial_id,)).fetchone():
        raise AdaptationConflict("Hay un envío en curso o de resultado incierto. Compruebe ese intento antes de continuar.")


def save_adaptation_content(db_path, trial_id, *, html, revision):
    """Save a validated editorial revision locally, using optimistic concurrency."""
    _id(trial_id)
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError("Cargue la adaptación antes de guardar sus cambios.")
    validation = validate_semantic_html(html)
    if not validation['valid']:
        raise ValueError("No se guardó el contenido: " + " ".join(validation['errors']))
    initialize_management(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = db.execute("""SELECT t.html,t.status,d.locale FROM generation_trials t
            JOIN generation_destinations d ON d.id=t.destination_id WHERE t.id=?""", (trial_id,)).fetchone() if 'generation_trials' in tables else None
        if not row or db.execute('SELECT 1 FROM adaptation_deletions WHERE trial_id=?', (trial_id,)).fetchone():
            raise KeyError(trial_id)
        if row['status'] != 'completed':
            raise AdaptationConflict("Solo se puede editar una adaptación completa.")
        _assert_idle(db, trial_id)
        if 'deployment_trials' in tables and db.execute("SELECT 1 FROM deployment_trials WHERE generation_trial_id=? AND status IN ('sending','unknown')", (trial_id,)).fetchone():
            raise AdaptationConflict("Hay un envío en curso o pendiente de comprobar.")
        if set(validation['cta_markers']) - set(CTA_NAMES.get(row['locale'], ())):
            raise ValueError("Los marcadores CTA no corresponden al idioma de esta adaptación.")
        current = effective_content(db, trial_id, row['html'])
        if current['content_revision'] != revision:
            raise AdaptationConflict("La adaptación cambió desde que la abrió. Recargue el contenido antes de guardar.")
        if current['html'] != html:
            db.execute('INSERT INTO adaptation_edits(trial_id,version,html,created_at) VALUES (?,?,?,?)',
                       (trial_id, current['edit_version'] + 1, html, _now()))
        return {'trial_id': trial_id, **effective_content(db, trial_id, row['html'])}


def delete_adaptation(db_path, trial_id):
    """Purge saved content, retaining IDs/usage and deduplication keys. No network."""
    _id(trial_id)
    initialize_management(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM adaptation_deletions WHERE trial_id=?", (trial_id,)).fetchone():
            return {"trial_id": trial_id, "deleted": True}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = db.execute("SELECT status,request_json FROM generation_trials WHERE id=?", (trial_id,)).fetchone() if 'generation_trials' in tables else None
        if not row:
            raise KeyError(trial_id)
        if row['status'] in ('running', 'unknown'):
            raise AdaptationConflict("La generación está en curso o pendiente de comprobar; no puede borrarse todavía.")
        _assert_idle(db, trial_id)
        if 'deployment_trials' in tables and db.execute("SELECT 1 FROM deployment_trials WHERE generation_trial_id=? AND status IN ('sending','unknown')", (trial_id,)).fetchone():
            raise AdaptationConflict("El primer envío está en curso o pendiente de comprobar.")
        request = json.loads(row['request_json'])
        options = {k: request[k] for k in ('model', 'service_tier', 'max_output_tokens', 'reasoning', 'store') if k in request}
        db.execute("""UPDATE generation_trials SET html='',response_json='{}',snapshot_json='{}',
            request_json=?,error='' WHERE id=?""", (_json(options), trial_id))
        if 'deployment_trials' in tables:
            db.execute("UPDATE deployment_trials SET payload_json='{}',warnings_json='[]',error='' WHERE generation_trial_id=?", (trial_id,))
        db.execute("UPDATE adaptation_actions SET payload_json='{}',error='' WHERE trial_id=?", (trial_id,))
        db.execute('DELETE FROM adaptation_edits WHERE trial_id=?', (trial_id,))
        if 'generation_html_formats' in tables:
            db.execute('DELETE FROM generation_html_formats WHERE trial_id=?', (trial_id,))
        if 'adaptation_category_overrides' in tables:
            db.execute('DELETE FROM adaptation_category_overrides WHERE trial_id=?', (trial_id,))
        db.execute("INSERT INTO adaptation_deletions(trial_id,deleted_at) VALUES (?,?)", (trial_id, _now()))
    return {"trial_id": trial_id, "deleted": True}


@contextmanager
def _client(session):
    client = session if session is not None else requests.Session()
    try:
        yield client
    finally:
        if session is None:
            client.close()


def _request(client, credentials, method, route, *, params=None, payload=None):
    return client.request(method, credentials.base_url + '/',
        params={'rest_route': '/wp/v2/' + route, **({'_et_check': str(time.time_ns())} if method == 'GET' else {}), **(params or {})},
        auth=(credentials.username, credentials.application_password),
        headers={'User-Agent': 'ET-Marketing/1.0', 'Accept': 'application/json', 'Cache-Control': 'no-cache'},
        json=payload, timeout=(10, 45), allow_redirects=False)


def _get(client, credentials, route, *, params=None, missing_post=False):
    try:
        response = _request(client, credentials, 'GET', route, params=params)
    except requests.RequestException:
        raise ValueError("No se pudo consultar WordPress. No se envió contenido.") from None
    try:
        data = response.json()
    except ValueError:
        raise ValueError("WordPress no devolvió una respuesta JSON válida. No se envió contenido.") from None
    if missing_post and response.status_code == 404 and isinstance(data, dict) and data.get('code') == 'rest_post_invalid_id':
        return None
    if response.status_code != 200:
        raise ValueError(f"WordPress respondió HTTP {int(response.status_code)} al comprobar el destino. No se envió contenido.")
    return data


def _latest_delivery(db, deployment):
    row = db.execute("SELECT id,result_json,updated_at FROM adaptation_actions WHERE trial_id=? AND status='completed' ORDER BY id DESC LIMIT 1",
                     (deployment['generation_trial_id'],)).fetchone()
    if row:
        deployment = {**deployment, 'result': json.loads(row[1]), 'updated_at': row[2], 'action_id': row[0]}
    return deployment


def _prepare(db_path, trial_id, env_path, client):
    trial = get_trial(db_path, trial_id)
    if trial['status'] != 'completed':
        raise ValueError("Solo se puede desplegar una adaptación completa.")
    deployment = get_deployment_trial(db_path, trial_id)
    if not deployment:
        raise ValueError("Esta adaptación todavía no tiene un envío previo con título e imagen configurados.")
    if deployment['status'] in ('sending', 'unknown'):
        raise AdaptationConflict("Primero debe comprobar el resultado del envío anterior.")
    with closing(sqlite3.connect(db_path)) as db:
        deployment = _latest_delivery(db, deployment)
    repository = DeploymentRepository(db_path, env_path=env_path)
    target = repository.get_target(deployment['target_id'])
    if target['destination_id'] != trial['destination_id'] or target['connection_key'] == 'canadians_fr' or target['language_mode'] != 'site_default':
        raise ValueError("El destino o su idioma no están preparados para este envío.")
    with closing(sqlite3.connect(db_path)) as db:
        destination = db.execute('SELECT site_url,locale,enabled FROM generation_destinations WHERE id=?', (trial['destination_id'],)).fetchone()
    if not destination or not destination[2] or destination[1] != target['locale'] or not _same_origin(destination[0], target['base_url']):
        raise ValueError("El destino cambió y no coincide con la conexión WordPress.")
    saved = deployment['payload']
    if not isinstance(saved.get('title'), str) or not saved['title'].strip() or len(saved['title']) > 300 or any(c in saved['title'] for c in '<>'):
        raise ValueError("El envío anterior no tiene un título válido.")
    media_id = _id(saved.get('featured_media'))
    validation = validate_semantic_html(trial['html'])
    if not validation['valid'] or set(validation['cta_markers']) - set(CTA_NAMES[target['locale']]):
        raise ValueError("La adaptación no supera la validación de HTML o idioma de los CTA.")
    prepared = prepare_wordpress_content(trial['html'], target['cta_links'])
    pending = not prepared['ready']
    credentials = load_credentials(target['connection_key'], env_path)
    if not credentials.username or not credentials.application_password:
        raise ValueError("Faltan las credenciales de WordPress.")
    post_id = deployment['result'].get('post_id')
    _id(post_id)
    user = _get(client, credentials, 'users/me', params={'context': 'edit'})
    if not isinstance(user, dict) or not isinstance(user.get('capabilities'), dict) or user['capabilities'].get('edit_posts') is not True:
        raise ValueError("WordPress no confirmó permisos de edición.")
    remote = _get(client, credentials, f'posts/{post_id}', params={'context': 'edit'}, missing_post=True)
    remote_status = remote.get('status') if isinstance(remote, dict) else None
    if remote is not None and (not isinstance(remote, dict) or type(remote.get('id')) is not int or remote['id'] != post_id or remote_status not in ('draft', 'publish', 'private', 'pending', 'future')):
        raise ValueError("No se pudo confirmar la entrada. Si está en la papelera, restáurela en WordPress antes de reenviar.")
    if pending and remote_status not in (None, 'draft'):
        raise ValueError("Hay CTA pendientes. Configure sus enlaces antes de actualizar una entrada que no sea borrador.")
    selection = trial_category_selection(db_path, trial_id, target['id'])
    if selection['category_ids']:
        categories, category_snapshot = selection['category_ids'], selection['context']
    else:
        # Preserve categories assigned in WordPress instead of replaying an old
        # empty delivery list, which would reset the post to its default term.
        categories = remote.get('categories') if remote is not None else saved.get('categories')
        category_snapshot = None
    try:
        category_check = validate_category_selection(db_path, target['id'], categories,
            context=category_snapshot, env_path=env_path, session=client, refresh=True)
    except ValueError as error:
        raise ValueError(str(error) + " Guarde las categorías de esta adaptación en el panel antes de volver a desplegar.") from None
    categories = category_check['category_ids']
    media = _get(client, credentials, f'media/{media_id}', params={'context': 'edit'})
    if not isinstance(media, dict) or type(media.get('id')) is not int or media['id'] != media_id or not str(media.get('mime_type', '')).startswith('image/') or not _same_origin(media.get('source_url'), target['base_url']):
        raise ValueError("La imagen guardada ya no es válida en el destino.")
    payload = {'title': saved['title'], 'content': prepared['content'] if not pending else trial['html'],
               'featured_media': media_id, 'categories': categories}
    if remote is None:
        payload.update(status='draft', slug=f"et-trial-{trial_id}-article-{trial['article_id']}")
    secrets = (credentials.application_password, credentials.application_password.replace(' ', ''))
    if any(secret and secret in _json(payload) for secret in secrets):
        raise ValueError("El contenido incluye información de autenticación.")
    remote_state = None if remote is None else {key: remote.get(key) for key in
        ('id', 'status', 'modified_gmt', 'featured_media', 'categories', 'slug')}
    if remote_state is not None:
        for key in ('content', 'title', 'excerpt'):
            value = remote.get(key)
            remote_state[key] = value.get('raw') if isinstance(value, dict) else value
    revision = hashlib.sha256(_json({'payload': payload, 'remote': remote_state, 'target_id': target['id'],
                                     'category_selection_revision': selection['revision'],
                                     'category_policy_revision': category_check['policy_revision'],
                                     'base_url': target['base_url'], 'prior_updated_at': deployment['updated_at']}).encode()).hexdigest()
    preview = {'trial_id': trial_id, 'revision': revision, 'mode': 'update' if remote is not None else 'create',
               'content_revision': trial['content_revision'],
               'category_selection_revision': selection['revision'],
               'category_policy_revision': category_check['policy_revision'],
               'post_id': post_id if remote is not None else None, 'remote_status': remote_status,
               'title': payload['title'], 'featured_media': media_id, 'categories': categories,
               'pending_ctas': pending, 'live_update': remote_status in ('publish', 'future'),
               'site_url': target['base_url'],
               'warnings': ['Se enviará la copia local y puede sustituir cambios hechos directamente en WordPress.',
                            'No se generará texto ni se llamará a OpenAI.']}
    return preview, payload, credentials, deployment


def preview_redeployment(db_path, trial_id, *, env_path=None, session=None):
    _id(trial_id)
    initialize_management(db_path)
    with closing(sqlite3.connect(db_path)) as db:
        _assert_idle(db, trial_id)
    with _client(session) as client:
        return _prepare(db_path, trial_id, env_path, client)[0]


def _action(row):
    return {'id': row['id'], 'trial_id': row['trial_id'], 'status': row['status'],
            'result': json.loads(row['result_json']), 'error': row['error'], 'updated_at': row['updated_at']}


def redeploy_adaptation(db_path, trial_id, *, request_key, revision, allow_pending_ctas=False,
                       confirm_live=False, env_path=None, session=None):
    _id(trial_id)
    if not isinstance(request_key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', request_key):
        raise ValueError("La acción necesita una clave única válida.")
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError("Compruebe el destino antes de confirmar el envío.")
    if type(allow_pending_ctas) is not bool or type(confirm_live) is not bool:
        raise ValueError("Las confirmaciones deben ser booleanas.")
    initialize_management(db_path)
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        previous = db.execute('SELECT * FROM adaptation_actions WHERE request_key=?', (request_key,)).fetchone()
        if previous:
            if previous['trial_id'] != trial_id or previous['revision'] != revision:
                raise AdaptationConflict("La clave ya corresponde a otra acción.")
            return _action(previous)
        _assert_idle(db, trial_id)
    with _client(session) as client:
        preview, payload, credentials, deployment = _prepare(db_path, trial_id, env_path, client)
        if preview['revision'] != revision:
            raise AdaptationConflict("La entrada o los datos locales cambiaron. Revise otra vez el destino antes de enviar.")
        if preview['pending_ctas'] and not allow_pending_ctas:
            raise ValueError("Confirme expresamente que el borrador puede conservar CTA pendientes.")
        if preview['live_update'] and not confirm_live:
            raise ValueError("Confirme expresamente la actualización de una entrada publicada o programada.")
        with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM adaptation_actions WHERE request_key=?', (request_key,)).fetchone()
            if previous:
                if previous['trial_id'] != trial_id or previous['revision'] != revision:
                    raise AdaptationConflict("La clave ya corresponde a otra acción.")
                return _action(previous)
            _assert_idle(db, trial_id)
            if db.execute('SELECT 1 FROM adaptation_deletions WHERE trial_id=?', (trial_id,)).fetchone():
                raise AdaptationConflict("La adaptación fue borrada durante la comprobación.")
            original = db.execute('SELECT html FROM generation_trials WHERE id=?', (trial_id,)).fetchone()
            if not original or effective_content(db, trial_id, original[0])['content_revision'] != preview['content_revision']:
                raise AdaptationConflict("El contenido local cambió durante la comprobación. Revise otra vez antes de enviar.")
            assert_category_state(db, get_trial(db_path, trial_id), deployment['target_id'],
                preview['category_selection_revision'], preview['category_policy_revision'])
            current = db.execute('SELECT updated_at FROM deployment_trials WHERE generation_trial_id=?', (trial_id,)).fetchone()
            latest = _latest_delivery(db, {'generation_trial_id': trial_id, 'updated_at': current[0] if current else None})
            if not current or latest['updated_at'] != deployment['updated_at']:
                raise AdaptationConflict("Otro envío cambió la entrada. Vuelva a comprobar el destino.")
            now = _now()
            action_id = db.execute("""INSERT INTO adaptation_actions
                (trial_id,request_key,revision,status,payload_json,created_at,updated_at)
                VALUES (?,?,?,'running',?,?,?)""", (trial_id,request_key,revision,_json(payload),now,now)).lastrowid
        status, error, result = 'unknown', 'No se pudo confirmar el resultado. No se reintentó el envío.', {}
        route = f"posts/{preview['post_id']}" if preview['mode'] == 'update' else 'posts'
        try:
            response = _request(client, credentials, 'POST', route, payload=payload)
            if response.status_code in (200, 201):
                data = response.json()
                expected_status = preview['remote_status'] if preview['mode'] == 'update' else 'draft'
                if (isinstance(data, dict) and type(data.get('id')) is int and data['id'] > 0
                        and (preview['mode'] == 'create' or data['id'] == preview['post_id'])
                        and data.get('status') == expected_status and data.get('featured_media') == payload['featured_media']
                        and isinstance(data.get('categories'), list)
                        and all(type(value) is int for value in data['categories'])
                        and sorted(set(data['categories'])) == payload['categories']):
                    result = {'post_id': data['id'], 'post_status': data['status'], 'featured_media': payload['featured_media'],
                              'categories': payload['categories'],
                              'edit_url': f"{credentials.base_url}/wp-admin/post.php?post={data['id']}&action=edit"}
                    status, error = 'completed', ''
            elif 400 <= response.status_code < 500:
                status, error = 'failed', f"WordPress rechazó el envío (HTTP {response.status_code}). No se reintentó."
        except (requests.RequestException, ValueError):
            pass
        with closing(sqlite3.connect(db_path)) as db, db:
            db.row_factory = sqlite3.Row
            now = _now()
            db.execute('UPDATE adaptation_actions SET status=?,result_json=?,error=?,updated_at=? WHERE id=?',
                       (status, _json(result), error, now, action_id))
            # Preserve the original delivery record; each subsequent attempt has its own journal row.
            return _action(db.execute('SELECT * FROM adaptation_actions WHERE id=?', (action_id,)).fetchone())
