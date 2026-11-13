"""Unit tests for scripts/effective-cost.py cost math."""

import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("effective_cost", ROOT / "scripts" / "effective-cost.py")
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""create table model_prices (model text primary key, prompt_per_1m real, completion_per_1m real,
        cache_per_1m real default 0, cache_read_per_1m real default 0, cache_creation_per_1m real default 0,
        prompt_configured int default 1, completion_configured int default 1,
        cache_read_configured int default 0, cache_creation_configured int default 0,
        source text, source_model_id text, raw_json text, updated_at_ms int, synced_at_ms int)""")
    db.execute("""create table model_price_context_tiers (model text, threshold_tokens int, prompt_per_1m real,
        completion_per_1m real, cache_per_1m real, cache_read_per_1m real, cache_creation_per_1m real,
        prompt_configured int default 1, completion_configured int default 1,
        cache_read_configured int default 0, cache_creation_configured int default 0)""")
    db.execute("insert into model_prices (model, prompt_per_1m, completion_per_1m, cache_per_1m, "
               "cache_read_per_1m, cache_read_configured) values ('m', 2.0, 10.0, 0.1, 0.1, 1)")
    db.execute("insert into model_price_context_tiers values ('m', 1000, 4.0, 15.0, 0.2, 0.2, 0, 1, 1, 1, 0)")
    now = 1_800_000_000_000
    db.execute("""create table usage_events (timestamp_ms int, auth_index text, auth_provider_snapshot text,
        requested_model text, resolved_model text, failed int, input_tokens int, cached_tokens int,
        cache_tokens int, cache_read_tokens int, cache_creation_tokens int,
        normalized_total_input_tokens int, normalized_cache_read_tokens int, normalized_cache_creation_tokens int,
        output_tokens int)""")
    events = [
        # 500 in w/ 300 cache-read at ≤1000 tier: (200*2 + 300*0.1 + 50*10)/1M = 0.00093
        (now, "a", "sub", "m", "m", 0, 500, 300, 300, 300, 0, 500, 300, 0, 50),
        # 1500 in >1000 tier: (1500*4 + 100*15)/1M = 0.0075
        (now + 1, "a", "sub", "m", "m", 0, 1500, 0, 0, 0, 0, 1500, 0, 0, 100),
        # requested-only match falls back: resolved unknown, requested m
        (now + 2, "a", "sub", "m", "unknown-model", 0, 100, 0, 0, 0, 0, 100, 0, 0, 100),
        (now + 3, "a", "sub", "m", "m", 1, 900, 0, 0, 0, 0, 900, 0, 0, 900),  # failed: excluded
    ]
    db.executemany("insert into usage_events values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", events)
    return db, now


def test_event_cost_math_and_tiers():
    db, now = make_db()
    prices, tiers, _ = ec.load_prices(db)
    evs = ec.load_events(db, 0, now + 10)
    c0, _ = ec.event_cost(evs[0], prices, tiers)
    c1, _ = ec.event_cost(evs[1], prices, tiers)
    assert abs(c0 - 0.00093) < 1e-9, c0
    assert abs(c1 - 0.0075) < 1e-9, c1
    c2, used = ec.event_cost(evs[2], prices, tiers)
    assert used == "unknown-model" and abs(c2 - 0.0012) < 1e-9  # billed via requested fallback
    db.close()


def test_pool_allocation_sums_to_fee():
    db, now = make_db()
    prices, tiers, book_hash = ec.load_prices(db)
    evs = ec.load_events(db, 0, now + 10)
    pool = {"id": "p", "billing": "subscription", "match": {"auth_provider_snapshot": ["sub"]}}
    rep = ec.report_pool(pool, evs, prices, tiers, 0, now + 10, book_hash, fee=20.0, now_ms=now + 5)
    assert rep["events"] == 3 and abs(rep["effective_usd"] - 20.0) < 1e-6, rep["effective_usd"]
    assert abs(rep["api_equivalent_usd"] - 0.00963) < 1e-6, rep["api_equivalent_usd"]
    assert rep["status"] == "provisional"
    db.close()


def test_metered_effective_equals_api_and_free_is_zero():
    db, now = make_db()
    prices, tiers, book_hash = ec.load_prices(db)
    evs = ec.load_events(db, 0, now + 10)
    metered = {"id": "m", "billing": "metered", "match": {"auth_provider_snapshot": ["sub"]}}
    rep = ec.report_pool(metered, evs, prices, tiers, 0, now + 10, book_hash, fee=None, now_ms=now + 10_000)
    assert abs(rep["effective_usd"] - rep["api_equivalent_usd"]) < 1e-12
    free = {"id": "f", "billing": "free", "match": {"auth_provider_snapshot": ["sub"]}}
    rep = ec.report_pool(free, evs, prices, tiers, 0, now + 10, book_hash, fee=None, now_ms=now + 10_000)
    assert rep["effective_usd"] == 0
    assert rep["status"] == "closed"
    db.close()


if __name__ == "__main__":
    test_event_cost_math_and_tiers()
    test_pool_allocation_sums_to_fee()
    test_metered_effective_equals_api_and_free_is_zero()
    print("ok")
