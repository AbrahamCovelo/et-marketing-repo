"""Offline end-to-end checks across the real cleaner, SQLite and export boundary."""
import json
import sqlite3

from src.extraction.cli import main


ARTICLE = '''<!doctype html><html lang="en-GB"><head><title>Source guide</title>
<link rel="canonical" href="https://movingtospain.com/sample-guide/">
</head><body><h1>Sample guide</h1><article class="single-entry">
<div class="entry-content single-content"><h2>Documents to prepare</h2>
<p>Applicants should check the requirements for their own situation before preparing
the application. Keep original documents and readable copies together, and check
the validity dates of each certificate with the relevant authority.</p>
<blockquote><p>My family used this service and had a wonderful time.</p>
<cite>Example Author, Writer at Moving to Spain</cite></blockquote>
<p>READ ALSO: <a href="/another-guide/">Read another guide</a></p>
</div></article></body></html>'''


def seed(tmp_path):
    source = tmp_path / 'source.html'
    source.write_text(ARTICLE, encoding='utf-8')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps([{'url': 'https://movingtospain.com/sample-guide/', 'path': str(source)}]), encoding='utf-8')
    database = tmp_path / 'articles.db'
    args = ['--db', str(database)]
    assert main(args + ['import-cache', str(manifest)]) == 0
    return args, database, manifest


def test_import_export_reclean_preserves_contract(tmp_path, capsys):
    args, database, manifest = seed(tmp_path)
    assert main(args + ['import-cache', str(manifest)]) == 0
    assert main(args + ['reclean']) == 0
    target = tmp_path / 'export.json'
    assert main(args + ['export', '--id', '1', '--output', str(target)]) == 0
    result = json.loads(target.read_text(encoding='utf-8'))
    assert 'Applicants should check' in result['content']['clean_text']
    assert 'Example Author' not in json.dumps(result)
    assert 'My family' not in json.dumps(result)
    assert 'another-guide' not in json.dumps(result)
    assert result['quality']['review_required'] is True
    assert result['gaps']
    with sqlite3.connect(database) as conn:
        assert conn.execute('select count(*) from source_articles').fetchone()[0] == 1
        assert conn.execute('select count(*) from article_versions').fetchone()[0] == 1
        assert conn.execute('pragma integrity_check').fetchone()[0] == 'ok'
        assert not conn.execute('pragma foreign_key_check').fetchall()


def test_one_bad_cache_entry_does_not_lose_valid_article(tmp_path, capsys):
    args, database, manifest = seed(tmp_path)
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    entries.insert(0, {'url': 'https://movingtospain.com/missing/', 'path': str(tmp_path / 'missing.html')})
    manifest.write_text(json.dumps(entries), encoding='utf-8')
    assert main(args + ['import-cache', str(manifest), '--refresh']) == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute('select count(*) from source_articles').fetchone()[0] == 1
        assert conn.execute('select count(*) from source_fetches where error is not null').fetchone()[0] == 1
        assert conn.execute('select status from crawl_runs order by id desc limit 1').fetchone()[0] == 'partial'


def test_corrupt_snapshot_does_not_replace_current_version(tmp_path, capsys):
    args, database, _ = seed(tmp_path)
    with sqlite3.connect(database) as conn:
        version, raw_path = conn.execute('select id,raw_path from article_versions').fetchone()
    (tmp_path / raw_path).write_bytes(b'corrupt snapshot')
    assert main(args + ['reclean']) == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute('select current_version_id from source_articles').fetchone()[0] == version
        assert conn.execute('select status from crawl_runs order by id desc limit 1').fetchone()[0] == 'failed'
