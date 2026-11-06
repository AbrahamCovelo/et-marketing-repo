import json
from datetime import datetime, timezone
import sqlite3

import pytest
import src.generation.usage as usage_module

from src.generation.usage import (
    audit_usage, estimate_usage, project_adaptation_cost, usage_fields, web_search_usage, trial_usage_summaries,
)


def usage(incoming=12000, cached=4000, writes=7000, outgoing=3000, reasoning=1000):
    return {"input_tokens": incoming, "input_tokens_details": {
        "cached_tokens": cached, "cache_write_tokens": writes},
        "output_tokens": outgoing, "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": incoming + outgoing}


SNAPSHOT = {"version": 1, "currency": "USD", "checked_at": "2026-11-06",
            "web_search_usd_per_call": "0.01", "models": [{
                "ids": ["test-model"], "maximum_input_tokens": 272000,
                "tiers": {"default": {"input": "1", "cached_input": "0.1",
                                      "cache_write_30m": "1.25", "cache_write_12h": "2.5",
                                      "output": "5"}}}]}


def test_requested_six_fields_preserve_missing_not_invented_zero():
    assert usage_fields(usage()) == {"entrada": 12000, "entradaCache": 4000,
                                    "escrituraCache": 7000, "salidaTotal": 3000,
                                    "razonamiento": 1000, "tokensTotales": 15000}
    assert usage_fields({"input_tokens": 30})["entradaCache"] is None


def test_trial_summary_includes_both_stages_and_the_six_metrics(database):
    path, prices = database
    with sqlite3.connect(path) as db:
        value = trial_usage_summaries(db, [2], snapshot_path=prices)[2]
    assert value['complete'] and value['totals']['calls'] == 2
    assert [r['stage'] for r in value['records']] == ['assessment', 'generation']
    assert value['totals']['usage_fields'] == {'entrada':24000,'entradaCache':8000,
        'escrituraCache':14000,'salidaTotal':6000,'razonamiento':2000,'tokensTotales':30000}
    expected = estimate_usage(usage(), model='test-model', service_tier='default', snapshot=SNAPSHOT)['estimated_cost_usd_max'] * 2
    assert value['totals']['estimated_cost_usd_max'] == pytest.approx(expected)
    assert value['billing_verified'] is False
    assert 'must-not-appear' not in json.dumps(value)


def test_missing_or_mismatched_assessment_cannot_look_like_complete_cost(database):
    path, prices = database
    with sqlite3.connect(path) as db:
        db.execute('UPDATE generation_assessments SET article_id=999 WHERE id=2')
        value = trial_usage_summaries(db, [2], snapshot_path=prices)[2]
    assert not value['complete'] and value['missing_assessment']
    assert value['totals']['calls'] == 1
    assert value['totals']['estimated_cost_usd_max'] is None


def test_standalone_legacy_trial_is_one_call_not_an_unrelated_assessment(database):
    path, prices = database
    with sqlite3.connect(path) as db:
        db.execute("UPDATE generation_trials SET run_key='legacy-first-test' WHERE id=2")
        value = trial_usage_summaries(db, [2], snapshot_path=prices)[2]
    assert value['complete'] and value['totals']['calls'] == 1
    assert not value['missing_assessment']


def test_audit_groups_complete_test_cost_and_keeps_deleted_test(database):
    path, prices = database
    result = audit_usage(path, snapshot_path=prices)
    assert len(result['trials']) == 2
    assert result['trials'][0]['deleted_locally'] is True
    assert all(r['totals']['calls'] == 2 for r in result['trials'])


def test_usage_never_exposes_undocumented_provider_values(database):
    path, prices = database
    raw = usage()
    raw['unexpected'] = 'private-provider-data'
    raw['input_tokens_details']['secret'] = 'private-provider-data'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE generation_trials SET usage_json=?', (json.dumps(raw),))
    assert 'private-provider-data' not in json.dumps(audit_usage(path, snapshot_path=prices))


def test_cache_writes_replace_input_and_reasoning_is_not_charged_twice():
    value = usage()
    value["input_tokens_details"]["cache_write_tokens_details"] = {
        "ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000}
    result = estimate_usage(value, model="test-model", service_tier="default", snapshot=SNAPSHOT)
    assert result["input_breakdown"] == {"ordinary": 1000, "cached": 4000,
                                          "cache_write_30m": 3000, "cache_write_12h": 4000}
    assert result["token_cost_usd"] == pytest.approx(.03015)
    value["output_tokens_details"]["reasoning_tokens"] = 2900
    assert estimate_usage(value, model="test-model", service_tier="default",
                          snapshot=SNAPSHOT)["token_cost_usd"] == result["token_cost_usd"]


def test_search_actions_chargeable_and_unfinished_search_is_uncertain():
    response = {"output": [{"type": "web_search_call", "status": status,
                            "action": {"type": kind}} for kind, status in [
        ("search", "completed"), ("open_page", "completed"),
        ("find_in_page", "completed"), ("search", "searching")]]}
    search = web_search_usage(response)
    assert search == {"completed_searches": 1, "unfinished_searches": 1,
                      "page_operations": 2, "unknown_actions": 0}
    result = estimate_usage(usage(), model="test-model", service_tier="default", snapshot=SNAPSHOT,
                            search=search)
    assert result["tool_cost_usd_min"] == .01 and result["tool_cost_usd_max"] == .02


@pytest.mark.parametrize("model,tier", [("unlisted-model", "default"), ("test-model", "priority")])
def test_unknown_model_or_tier_is_unpriced_instead_of_free(model, tier):
    result = estimate_usage(usage(), model=model, service_tier=tier, snapshot=SNAPSHOT)
    assert result["estimated_cost_usd_min"] is None
    assert result["warnings"]


def test_invalid_partition_or_unverified_context_refuses_price():
    for value in (usage(incoming=9000), usage(incoming=300000)):
        assert estimate_usage(value, model="test-model", service_tier="default",
                              snapshot=SNAPSHOT)["estimated_cost_usd_min"] is None


def test_expired_promo_refuses_future_cost_but_preserves_historical_audit():
    snapshot = json.loads(json.dumps(SNAPSHOT))
    snapshot["models"][0]["valid_until"] = "2026-11-30"
    assert estimate_usage(usage(), model="test-model", service_tier="default", snapshot=snapshot,
                          created_at="2026-12-01")["estimated_cost_usd_min"] is None
    assert estimate_usage(usage(), model="test-model", service_tier="default", snapshot=snapshot,
                          created_at="2026-11-06")["estimated_cost_usd_min"] is not None


@pytest.fixture
def database(tmp_path):
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as connection:
        for table in ("generation_trials", "generation_assessments"):
            connection.execute(f"""CREATE TABLE {table} (id INTEGER PRIMARY KEY, run_key TEXT,
                article_id INTEGER, configuration_id INTEGER, model TEXT, status TEXT, request_json TEXT,
                response_json TEXT, usage_json TEXT, created_at TEXT)""")
            stage = "generation" if table == "generation_trials" else "assessment"
            for identifier in (1, 2):
                connection.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    identifier, f"panel-{identifier}." + stage, 60, 3, "test-model", "completed",
                    json.dumps({"service_tier": "default", "secret": "must-not-appear"}),
                    json.dumps({"service_tier": "default"}) if identifier == 2 else "{}",
                    json.dumps(usage()), f"2026-11-0{identifier+4}T15:00:00+00:00"))
        connection.execute("CREATE TABLE adaptation_deletions (trial_id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO adaptation_deletions VALUES(1)")
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
    return db, prices


def test_read_only_audit_keeps_deleted_usage_and_combines_only_paired_assessment(database):
    db, prices = database
    before = db.read_bytes()
    result = audit_usage(db, snapshot_path=prices)
    assert result["totals"]["calls"] == 4
    assert result["totals"]["usage_fields"]["entrada"] == 48000
    assert sum(r["deleted_locally"] for r in result["records"]) == 1
    assert result["totals"]["unconfirmed_service_tier_calls"] == 2
    assert "must-not-appear" not in json.dumps(result)
    paired = audit_usage(db, snapshot_path=prices, trial_id=2)
    assert {(r["stage"], r["id"]) for r in paired["records"]} == {("assessment", 2), ("generation", 2)}
    assert db.read_bytes() == before


def test_missing_database_does_not_create_it(tmp_path):
    path = tmp_path / "missing.db"
    assert audit_usage(path)["totals"]["calls"] == 0
    assert not path.exists()


def test_projection_uses_latest_pair_and_does_not_claim_cap(database):
    db, prices = database
    result = project_adaptation_cost(db, model="test-model", adaptations=300, snapshot_path=prices)
    assert result["sample_trial_id"] == 2
    assert result["stages_per_adaptation"] == 2
    assert result["estimated_cost_usd_min"] == pytest.approx(result["per_adaptation_usd_min"] * 300)
    assert "No es un límite" in result["notice"]
    unknown = project_adaptation_cost(db, model="unknown", adaptations=300, snapshot_path=prices)
    assert unknown["estimated_cost_usd_min"] is None


def test_projection_checks_present_date_not_historical_sample_date(database, monkeypatch):
    db, prices = database
    snapshot = json.loads(prices.read_text())
    snapshot["models"][0]["valid_until"] = "2026-11-30"
    prices.write_text(json.dumps(snapshot), encoding="utf-8")
    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 12, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(usage_module, "datetime", FutureClock)
    historical = audit_usage(db, snapshot_path=prices)
    assert historical["totals"]["estimated_cost_usd_min"] is not None
    future = project_adaptation_cost(db, model="test-model", snapshot_path=prices)
    assert future["estimated_cost_usd_min"] is None
    assert any("vencido" in warning for warning in future["warnings"])


@pytest.mark.parametrize("model,tier,expected_min,expected_max", [
    ("gpt-5.6-sol", "default", .37609, .38609),
    ("gpt-6-luna", "default", .02890225, .03890225),
    ("gpt-6-luna", "flex", .024451125, .034451125),
])
def test_verified_november_reference_two_stages_and_projection(model, tier, expected_min, expected_max):
    # Saved numeric response usage from the retirement article, no network or live DB.
    assessment = usage(35984, 0, 10792, 3974, 990)
    generation = usage(8445, 0, 8442, 3983, 511)
    costs = [estimate_usage(value, model=model, service_tier=tier, created_at="2026-11-06",
                            search={"completed_searches": 2, "unfinished_searches": 1} if index == 0 else {})
             for index, value in enumerate((assessment, generation))]
    assert sum(c["estimated_cost_usd_min"] for c in costs) == pytest.approx(expected_min)
    assert sum(c["estimated_cost_usd_max"] for c in costs) == pytest.approx(expected_max)
    assert sum(c["estimated_cost_usd_max"] for c in costs) * 300 == pytest.approx(expected_max * 300)
