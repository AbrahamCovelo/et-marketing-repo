"""Read the current local editorial revision without changing model output."""
import hashlib
import json


def effective_content(connection, trial_id, original_html):
    row = None
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='adaptation_edits'").fetchone():
        row = connection.execute(
            "SELECT version,html,created_at FROM adaptation_edits WHERE trial_id=? ORDER BY version DESC LIMIT 1",
            (trial_id,),
        ).fetchone()
    version, html, edited_at = tuple(row) if row is not None else (0, original_html, None)
    normalization = None
    if not version and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='generation_html_formats'").fetchone():
        source_hash = hashlib.sha256(original_html.encode('utf-8')).hexdigest()
        formatted = connection.execute("""SELECT html,normalized_sha256,report_json,created_at
            FROM generation_html_formats WHERE trial_id=? AND source_sha256=? ORDER BY id DESC LIMIT 1""",
            (trial_id, source_hash)).fetchone()
        if formatted and hashlib.sha256(formatted[0].encode('utf-8')).hexdigest() == formatted[1]:
            html = formatted[0]
            normalization = {**json.loads(formatted[2]), 'created_at': formatted[3]}
    revision = hashlib.sha256(f"{trial_id}:{version}:".encode() + html.encode('utf-8')).hexdigest()
    return {'html': html, 'content_revision': revision, 'edit_version': version,
            'edited_at': edited_at, 'html_source': 'editor' if version else 'normalized' if normalization else 'model',
            'normalization': normalization}
