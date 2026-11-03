"""Command-line entry point for the text-only staging pipeline."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .cleaner import extract_html
from .db_manager import Database
from .scraper import discover_guides, scrape_articles


def emit(value, output=None):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)


def parser():
    p = argparse.ArgumentParser(description='Extraer y almacenar guías de Moving to Spain (solo texto).')
    p.add_argument('--db', default='data/source_articles.db', help='Ruta de la base SQLite.')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='Crear el esquema de la base de datos.')
    for name in ('discover', 'scrape'):
        sub = commands.add_parser(name, help='Descubrir URLs.' if name == 'discover' else 'Extraer y guardar artículos.')
        sub.add_argument('--max-pages', type=int, default=3, help='Máximo de páginas del índice (predeterminado: 3).')
        sub.add_argument('--all-pages', action='store_true', help='Recorrer toda la paginación de /guides/.')
        sub.add_argument('--limit', type=int, help='Máximo de artículos.')
        if name == 'discover':
            sub.add_argument('--output', help='Guardar lista JSON de URLs.')
        else:
            sub.add_argument('--url', action='append', help='URL explícita; se puede repetir.')
            sub.add_argument('--url-file', help='Lista JSON de URLs o archivo de una URL por línea.')
            sub.add_argument('--refresh', action='store_true', help='Descargar también artículos ya almacenados.')
            sub.add_argument('--delay', type=float, default=1.0, help='Pausa mínima entre peticiones, en segundos.')
            sub.add_argument('--timeout', type=float, default=30.0, help='Tiempo de espera por petición.')
    cached = commands.add_parser('import-cache', help='Limpiar y almacenar HTML local desde un manifiesto JSON.')
    cached.add_argument('manifest', help='Lista de objetos con url y path (rutas relativas al directorio de trabajo).')
    cached.add_argument('--refresh', action='store_true', help='Reprocesar artículos existentes sin red.')
    reclean = commands.add_parser('reclean', help='Repetir la limpieza desde los respaldos de la base, sin red.')
    reclean.add_argument('--id', type=int, dest='article_id', help='Artículo concreto; por defecto todos.')
    commands.add_parser('stats', help='Mostrar estadísticas de almacenamiento.')
    commands.add_parser('list', help='Listar los artículos almacenados.')
    export = commands.add_parser('export', help='Exportar contenido estructurado para la futura adaptación.')
    identity = export.add_mutually_exclusive_group(required=True)
    identity.add_argument('--id', type=int, dest='article_id')
    identity.add_argument('--url')
    export.add_argument('--output', help='Destino UTF-8; por defecto imprime JSON.')
    export.add_argument('--text', action='store_true', help='Exportar únicamente texto de lectura, sin estructura ni avisos.')
    return p


def import_cache(db, manifest_path, refresh):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    if not isinstance(manifest, list):
        raise ValueError('El manifiesto debe ser una lista JSON.')
    run_id = db.start_run('import-cache', {'manifest': str(manifest_path), 'refresh': refresh})
    stats = {'run_id': run_id, 'success': 0, 'skipped': 0, 'failed': 0, 'errors': []}
    interrupted = True
    try:
        for item in manifest:
            url = item.get('url', '') if isinstance(item, dict) else ''
            try:
                if not url:
                    raise ValueError('Cada entrada necesita url y path.')
                if not refresh and db.has_article(url):
                    stats['skipped'] += 1
                    continue
                html = Path(item['path']).read_text(encoding='utf-8')
                result = extract_html(html, url)
                db.store_article(result, raw_html=html, requested_url=url, run_id=run_id, status_code=None)
                stats['success'] += 1
            except Exception as exc:
                stats['failed'] += 1
                stats['errors'].append({'url': url, 'error': str(exc)})
                db.record_fetch(run_id, url, error=str(exc))
        interrupted = False
    finally:
        status = 'failed' if interrupted or (stats['failed'] and not stats['success']) else 'partial' if stats['failed'] else 'completed'
        db.finish_run(run_id, stats, status)
    return stats


def reclean_articles(db, article_id=None):
    articles = [row for row in db.list_articles() if article_id is None or row['id'] == article_id]
    if not articles:
        raise ValueError('No hay artículos que reprocesar.')
    run_id = db.start_run('reclean', {'article_id': article_id})
    stats = {'run_id': run_id, 'success': 0, 'skipped': 0, 'failed': 0, 'errors': []}
    interrupted = True
    try:
        for article in articles:
            url = article['canonical_url']
            try:
                snapshot = db.get_snapshot(article['id'])
                result = extract_html(snapshot['html'], snapshot['url'])
                db.store_article(result, raw_html=snapshot['html'], requested_url=url, run_id=run_id, status_code=None)
                stats['success'] += 1
            except Exception as exc:
                stats['failed'] += 1
                stats['errors'].append({'url': url, 'error': str(exc)})
                db.record_fetch(run_id, url, error=str(exc))
        interrupted = False
    finally:
        status = 'failed' if interrupted or (stats['failed'] and not stats['success']) else 'partial' if stats['failed'] else 'completed'
        db.finish_run(run_id, stats, status)
    return stats


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    p = parser()
    args = p.parse_args(argv)
    for field in ('max_pages', 'limit', 'timeout'):
        value = getattr(args, field, None)
        if value is not None and value <= 0:
            p.error(f'--{field.replace("_", "-")} debe ser mayor que cero.')
    if getattr(args, 'delay', 0) < 0:
        p.error('--delay no puede ser negativo.')
    db = None
    try:
        db = Database(args.db)
        if args.command == 'init':
            emit({'database': str(Path(args.db).resolve()), 'status': 'initialized'})
        elif args.command in ('stats', 'list'):
            emit(db.stats() if args.command == 'stats' else db.list_articles())
        elif args.command == 'export':
            result = db.get_export(url=args.url, article_id=args.article_id)
            emit(result['content']['clean_text'] if args.text else result, args.output)
        elif args.command == 'import-cache':
            stats = import_cache(db, args.manifest, args.refresh)
            emit(stats)
            return 1 if stats['failed'] else 0
        elif args.command == 'reclean':
            stats = reclean_articles(db, args.article_id)
            emit(stats)
            return 1 if stats['failed'] else 0
        else:
            urls = list(getattr(args, 'url', None) or [])
            if getattr(args, 'url_file', None):
                raw = Path(args.url_file).read_text(encoding='utf-8-sig')
                extra = json.loads(raw) if raw.lstrip().startswith('[') else raw.splitlines()
                if not isinstance(extra, list) or not all(isinstance(u, str) for u in extra):
                    raise ValueError('--url-file debe contener una lista de URLs.')
                urls.extend(u.strip() for u in extra if u.strip())
            if not urls:
                print('Descubriendo guías...', file=sys.stderr, flush=True)
                urls = discover_guides(max_pages=None if args.all_pages else args.max_pages)
            urls = list(dict.fromkeys(urls))
            if args.limit:
                urls = urls[:args.limit]
            if args.command == 'discover':
                emit(urls, args.output)
            else:
                print(f'Procesando {len(urls)} artículos...', file=sys.stderr, flush=True)
                stats = scrape_articles(urls, db, refresh=args.refresh, delay=args.delay, timeout=args.timeout)
                emit(stats)
                return 1 if stats['failed'] else 0
        return 0
    except (ValueError, OSError, RuntimeError, LookupError, sqlite3.Error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())
