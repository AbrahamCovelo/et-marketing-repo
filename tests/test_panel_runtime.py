import json
import sqlite3

import pytest

from src.dashboard.runtime import install_legacy_guards, panel_instance


def test_only_one_panel_for_a_database_and_lock_releases(tmp_path):
    path = tmp_path / 'source.db'
    with panel_instance(path):
        with pytest.raises(ValueError, match='Ya hay un panel'):
            with panel_instance(path):
                pytest.fail('Second instance started')
        with panel_instance(tmp_path / 'other.db'):
            pass
    with panel_instance(path):
        pass


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE panel_generation_runs(id INTEGER PRIMARY KEY,payload_json TEXT)')
        db.execute('CREATE TABLE deployment_trials(id INTEGER PRIMARY KEY,target_id INTEGER,status TEXT,payload_json TEXT)')
        db.execute('CREATE TABLE wordpress_category_policies(target_id INTEGER,allowed_category_ids_json TEXT,excluded_default_category_id INTEGER)')
        db.execute("INSERT INTO wordpress_category_policies VALUES (9,'[901,902]',77)")
        db.execute("INSERT INTO deployment_trials VALUES (1,9,'draft','{\"categories\":[]}')")
    install_legacy_guards(path)
    install_legacy_guards(path)
    return path


@pytest.mark.parametrize('payload', [{}, {'budget_quote_id': None},
    {'budget_quote_id': 'a'*32}, {'budget_quote_id': 'a'*32, 'service_tier':'auto'}])
def test_old_process_cannot_reserve_an_unquoted_paid_run(database, payload):
    with sqlite3.connect(database) as db:
        with pytest.raises(sqlite3.IntegrityError, match='Panel desactualizado'):
            db.execute('INSERT INTO panel_generation_runs(payload_json) VALUES (?)', (json.dumps(payload),))


def test_current_run_and_legacy_read_only_history_are_accepted(database):
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO panel_generation_runs(payload_json) VALUES (?)',
                   (json.dumps({'budget_quote_id':'a'*32,'service_tier':'flex'}),))
        assert db.execute('SELECT payload_json FROM deployment_trials WHERE id=1').fetchone()[0] == '{"categories":[]}'
        db.execute("UPDATE deployment_trials SET payload_json='{}' WHERE id=1")


@pytest.mark.parametrize('categories', [None, [], [77], [901,77], [999], ['901'], [True]])
def test_empty_default_unknown_or_noninteger_categories_block_reservation(database, categories):
    with sqlite3.connect(database) as db:
        with pytest.raises(sqlite3.IntegrityError, match='Despliegue bloqueado'):
            db.execute("INSERT INTO deployment_trials(target_id,status,payload_json) VALUES(9,'sending',?)",
                       (json.dumps({'categories':categories}),))


def test_valid_dynamic_categories_can_be_reserved(database):
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO deployment_trials(target_id,status,payload_json) VALUES(9,'sending',?)",
                   (json.dumps({'categories':[901,902]}),))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO deployment_trials(target_id,status,payload_json) VALUES(20,'sending',?)",
                       (json.dumps({'categories':[901]}),))
