"""Cover Synthetic's weekly-only quota response and failed refreshes."""

import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "synthetic_usage", Path(__file__).with_name("export-synthetic-usage.py")
)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

QUOTA = {
    "planWeeklyLimit": {
        "percentRemaining": 83.45,
        "nextRegenAt": "2026-11-08T12:00:00Z",
        "nextRegenPercent": 2,
    },
    # These counters must not become a five-hour usage meter.
    "subscription": {"limit": 500, "requests": 0},
    "search": {"hourly": {"limit": 250, "requests": 0}},
}


class SyntheticExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "usage.json"
        env = patch.dict(os.environ, {
            "SYNTHETIC_API_KEY": "test-api-secret",
            "SYNTHETIC_USAGE_OUTPUT": str(self.output),
        })
        env.start()
        self.addCleanup(env.stop)

    def response(self, data):
        return patch.object(exporter.urllib.request, "urlopen",
                            return_value=BytesIO(json.dumps(data).encode()))

    def test_weekly_only_response_exports_percentage_and_regeneration(self):
        with self.response(QUOTA) as opener, patch.object(exporter.time, "time", return_value=123):
            exporter.main()
        self.assertEqual(json.loads(self.output.read_text()), {
            "weekly_remaining": 83.5,
            "weekly_next_refill_at": 1794139200,
            "weekly_next_refill_percent": 2,
            "weekly_next_refill_effective_percent": 2,
            "weekly_next_refill_remaining": 85.5,
            "weekly_refill_interval_seconds": 12120,
            "weekly_refills_until_full": 9,
            "weekly_full_refill_at": 1794236160,
            "sampled_at": 123,
        })
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("test-api-secret", self.output.read_text())
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, exporter.URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-api-secret")

    def test_small_percentages_are_percentage_points(self):
        payload = deepcopy(QUOTA)
        payload["planWeeklyLimit"]["percentRemaining"] = 0.45
        with self.response(payload):
            self.assertEqual(exporter.get_usage()["weekly_remaining"], 0.5)

    def test_does_not_require_legacy_or_search_counters(self):
        with self.response({"planWeeklyLimit": QUOTA["planWeeklyLimit"]}):
            self.assertEqual(exporter.get_usage()["weekly_remaining"], 83.5)

    def test_full_estimate_counts_discrete_ticks_using_unrounded_balance(self):
        for remaining, ticks, full_at in (
            (98, 1, 1794139200),
            (98.01, 1, 1794139200),
            (97.99, 2, 1794151320),
            (99.96, 1, 1794139200),
            (0, 50, 1794733080),
            (100, 0, None),
        ):
            payload = deepcopy(QUOTA)
            payload["planWeeklyLimit"]["percentRemaining"] = remaining
            with self.subTest(remaining=remaining), self.response(payload), \
                    patch.object(exporter.time, "time", return_value=1794135600):
                usage = exporter.get_usage()
            self.assertEqual(usage["weekly_refills_until_full"], ticks)
            self.assertEqual(usage["weekly_full_refill_at"], full_at)
            self.assertLessEqual(usage["weekly_next_refill_remaining"], 100)
            self.assertAlmostEqual(usage["weekly_next_refill_effective_percent"], min(100 - remaining, 2))

    def test_no_full_estimate_for_zero_refill_or_past_tick(self):
        for increment, now in ((0, 1794135600), (2, 1794139200)):
            payload = deepcopy(QUOTA)
            payload["planWeeklyLimit"]["nextRegenPercent"] = increment
            with self.subTest(increment=increment, now=now), self.response(payload), \
                    patch.object(exporter.time, "time", return_value=now):
                usage = exporter.get_usage()
            self.assertIsNone(usage["weekly_refills_until_full"])
            self.assertIsNone(usage["weekly_full_refill_at"])
            self.assertEqual(usage["weekly_remaining"], 83.5)

    def test_invalid_weekly_data_preserves_last_good_sample(self):
        self.output.write_text('{"sampled_at":1,"weekly_remaining":80}')
        original = self.output.read_bytes()
        cases = [(field, value)
                 for field in ("percentRemaining", "nextRegenPercent")
                 for value in (-1, 101, float("nan"), float("inf"), None)]
        cases += [("nextRegenAt", "bad-time"),
                  ("nextRegenAt", "2026-11-08T12:00:00")]
        for field, value in cases:
            payload = deepcopy(QUOTA)
            payload["planWeeklyLimit"][field] = value
            with self.subTest(field=field, value=value), self.response(payload):
                with self.assertRaises((ValueError, TypeError)):
                    exporter.main()
                self.assertEqual(self.output.read_bytes(), original)
        with self.response({"subscription": QUOTA["subscription"]}):
            with self.assertRaises(KeyError):
                exporter.main()
        self.assertEqual(self.output.read_bytes(), original)

    def test_http_error_preserves_sample_without_exposing_key(self):
        self.output.write_text('{"sampled_at":1}')
        error = urllib.error.HTTPError(exporter.URL, 401, "unauthorized", {}, None)
        self.addCleanup(error.close)
        with patch.object(exporter.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                exporter.main()
        self.assertNotIn("test-api-secret", str(raised.exception))
        self.assertEqual(self.output.read_text(), '{"sampled_at":1}')


if __name__ == "__main__":
    unittest.main()
