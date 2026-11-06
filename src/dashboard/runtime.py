"""One server per database, plus durable rejection of obsolete write contracts."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import sys


@contextmanager
def panel_instance(db_path):
    """OS lock releases on exit/crash; lock files contain no credentials."""
    lock_path = Path(db_path).resolve().with_suffix('.panel.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Ya hay un panel abierto para esta base. Use esa instancia o cierre su consola antes de reiniciarlo.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def install_legacy_guards(db_path):
    """Protect reservations, before external requests, even in an old process.

    Only future inserts are constrained. Existing audit records stay intact.
    The current application's full quote/category validation still applies.
    """
    with closing(sqlite3.connect(db_path, timeout=30)) as db, db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'panel_generation_runs' in tables:
            db.execute('''CREATE TRIGGER IF NOT EXISTS panel_require_cost_quote_v1
                BEFORE INSERT ON panel_generation_runs
                WHEN COALESCE(json_type(NEW.payload_json,'$.budget_quote_id'),'') != 'text'
                  OR length(json_extract(NEW.payload_json,'$.budget_quote_id')) != 32
                  OR COALESCE(json_extract(NEW.payload_json,'$.service_tier'),'') NOT IN ('default','flex')
                BEGIN SELECT RAISE(ABORT,'Panel desactualizado: reinicie antes de generar. Falta presupuesto.'); END''')
        if {'deployment_trials', 'wordpress_category_policies'} <= tables:
            db.execute('''CREATE TRIGGER IF NOT EXISTS deployment_require_categories_v1
                BEFORE INSERT ON deployment_trials
                WHEN NEW.status='sending' AND (
                    COALESCE(json_type(NEW.payload_json,'$.categories'),'') != 'array'
                    OR json_array_length(NEW.payload_json,'$.categories')=0
                    OR NOT EXISTS(SELECT 1 FROM wordpress_category_policies p WHERE p.target_id=NEW.target_id)
                    OR EXISTS(SELECT 1 FROM json_each(NEW.payload_json,'$.categories') chosen
                        WHERE chosen.type != 'integer' OR NOT EXISTS(
                            SELECT 1 FROM wordpress_category_policies p,
                                json_each(p.allowed_category_ids_json) allowed
                            WHERE p.target_id=NEW.target_id AND allowed.value=chosen.value
                              AND chosen.value != p.excluded_default_category_id)))
                BEGIN SELECT RAISE(ABORT,'Despliegue bloqueado: faltan categorias permitidas. Reinicie el panel.'); END''')
