"""Tests for export-cpamp-usage.py: daily freezing, point-in-time pricing and HA feed."""

import datetime as dt
import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("export_cpamp_usage", HERE / "export-cpamp-usage.py")
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)

TZ = ZoneInfo("America/New_York")


def at(day, hour):
    return dt.datetime(2026, 11, day, hour, tzinfo=TZ)


class Fixture:
    def __init__(self, root):
        self.db_path = root / "usage.sqlite"
        self.archive = root / "archive"
        self.output = root / "ha.json"
        root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.execute("""create table model_prices (model text primary key, prompt_per_1m real, completion_per_1m real,
            cache_per_1m real default 0, cache_read_per_1m real default 0, cache_creation_per_1m real default 0,
            prompt_configured int default 1, completion_configured int default 1,
            cache_read_configured int default 0, cache_creation_configured int default 0,
            source text, source_model_id text, raw_json text, updated_at_ms int, synced_at_ms int)""")
        db.execute("create table model_price_context_tiers (model text, threshold_tokens int)")
        db.execute("create table api_key_aliases (api_key_hash text primary key, alias text, updated_at_ms int)")
        db.execute("""create table usage_events (timestamp_ms int, requested_model text, resolved_model text,
            failed int, auth_provider_snapshot text, api_key_hash text, session_id text, input_tokens int,
            cached_tokens int, cache_tokens int, cache_read_tokens int, cache_creation_tokens int,
            normalized_total_input_tokens int, normalized_cache_read_tokens int,
            normalized_cache_creation_tokens int, output_tokens int, reasoning_tokens int,
            latency_ms int, ttft_ms int)""")
        db.execute("insert into model_prices (model, prompt_per_1m, completion_per_1m, cache_read_per_1m, "
                   "cache_read_configured) values ('claude-opus-x', 2.0, 10.0, 0.2, 1)")
        db.execute("insert into api_key_aliases values ('aaaaaaaa1111', 'laptop agent', 0)")
        db.commit()
        db.close()

    def event(self, when, model="claude-opus-x", inp=1_000_000, cache_read=0, out=100_000, failed=0, key="aaaaaaaa1111"):
        db = sqlite3.connect(self.db_path)
        db.execute("insert into usage_events values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (int(when.timestamp() * 1000), model, model, failed, "claude", key, "s1", inp, cache_read, 0,
                    cache_read, 0, inp, cache_read, 0, out, 0, 1000, 100))
        db.commit()
        db.close()

    def set_price(self, prompt):
        db = sqlite3.connect(self.db_path)
        db.execute("update model_prices set prompt_per_1m = ?", (prompt,))
        db.commit()
        db.close()

    def run(self, now):
        return ex.run(self.db_path, self.archive, self.output, TZ, now=now)

    def day(self, iso):
        return json.loads((self.archive / "days" / iso[:4] / f"{iso}.json").read_text())

    def feed(self):
        return json.loads(self.output.read_text())


class ExportCpampUsageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.f = Fixture(pathlib.Path(tmp.name))

    def test_cost_tokens_and_cache_breakdown(self):
        # 1M input of which 600k cache reads, 100k output:
        # 400k*2 + 600k*0.2 + 100k*10 = 0.8 + 0.12 + 1.0 = $1.92; savings 600k*(2-0.2) = $1.08
        self.f.event(at(23, 10), cache_read=600_000)
        self.f.event(at(23, 11), failed=1)
        self.f.event(at(24, 10))
        self.f.run(at(24, 12))
        t = self.f.day("2026-11-23")["totals"]
        self.assertAlmostEqual(t["api_equivalent_usd"], 1.92)
        self.assertAlmostEqual(t["cache_savings_usd"], 1.08)
        self.assertEqual((t["requests"], t["failed_requests"]), (1, 1))
        self.assertEqual((t["cache_read_tokens"], t["uncached_input_tokens"]), (600_000, 400_000))
        self.assertEqual(t["cache_hit_rate"], 0.6)
        client = self.f.day("2026-11-23")["by_client"]["key:aaaaaaaa"]
        self.assertEqual(client["alias"], "laptop agent")

    def test_frozen_days_keep_their_price_after_a_price_change(self):
        self.f.event(at(23, 10))
        self.f.event(at(24, 10))
        self.assertEqual(self.f.run(at(24, 12)), 1)  # only the 23rd is closed and past grace
        before = (self.f.archive / "days/2026/2026-11-23.json").read_bytes()
        self.f.set_price(20.0)
        self.f.run(at(24, 13))
        self.assertEqual((self.f.archive / "days/2026/2026-11-23.json").read_bytes(), before)
        feed = self.f.feed()
        # 23rd frozen at $3 (1M*2 + 100k*10); the open 24th is priced live at $21.
        self.assertAlmostEqual(feed["totals"]["api_equivalent_usd"], 3.0 + 21.0)
        self.assertEqual(feed["last_frozen_day"], "2026-11-23")
        self.assertEqual(len(list((self.f.archive / "prices").glob("*.json"))), 1)

    def test_grace_period_and_stalled_collector(self):
        self.f.event(at(23, 10))
        self.f.event(at(24, 1))
        self.assertEqual(self.f.run(at(24, 3)), 0)  # within the 6h grace window
        self.assertEqual(self.f.run(at(24, 7)), 1)
        f2 = Fixture(self.f.archive.parent / "stalled")
        f2.event(at(20, 10))  # no later events: wait for the stall limit
        self.assertEqual(f2.run(at(21, 12)), 0)
        self.assertEqual(f2.run(at(24, 1)), 1)  # each quiet day waits its own 3 days

    def test_days_with_unpriced_models_stay_open_until_priced_or_hold_expires(self):
        self.f.event(at(10, 10), model="new-model")
        self.f.event(at(10, 11))
        self.f.event(at(12, 10))
        self.f.run(at(12, 12))
        self.assertEqual(self.f.feed()["last_frozen_day"], "2026-11-11")
        self.assertFalse((self.f.archive / "days/2026/2026-11-10.json").exists())
        self.f.run(at(25, 12))
        self.assertEqual(self.f.day("2026-11-10")["totals"]["unpriced_requests"], 1)

    def test_families_and_rolling_rates(self):
        self.f.event(at(23, 10), cache_read=500_000)
        self.f.event(at(23, 11), model="hf:moonshotai/Kimi-K9", out=0)
        self.f.event(at(24, 10), model="gpt-9-sol")
        self.f.run(at(25, 9))
        feed = self.f.feed()
        self.assertEqual(feed["families"]["claude_opus"]["cache_read_tokens"], 500_000)
        self.assertEqual(feed["families"]["kimi"]["input_tokens"], 1_000_000)
        self.assertEqual(feed["families"]["gpt"]["requests"], 1)
        self.assertEqual(feed["rolling_7d"]["claude_opus"]["cache_hit_pct"], 50.0)
        self.assertAlmostEqual(feed["rolling_7d"]["totals"]["unpriced_token_pct"], 100 * 2_100_000 / 3_200_000, 2)
        self.assertEqual(feed["yesterday"]["date"], "2026-11-24")

    def test_ha_example_files_match_generator(self):
        for kind in ("command_line", "template"):
            path = HERE.parent / "cpamp-usage" / f"{kind}.example.yaml"
            self.assertEqual(path.read_text(), ex.ha_yaml(kind), f"regenerate {path.name} with --print-ha-yaml {kind}")


if __name__ == "__main__":
    unittest.main()
