"""Check billing aggregation, date boundaries, and preservation of good samples."""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


spec = importlib.util.spec_from_file_location(
    "fal_usage", Path(__file__).with_name("export-fal-usage.py"))
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def page(*costs, cursor=None):
    return {"summary": [{"cost_total": cost, "currency": "USD"} for cost in costs],
            "has_more": cursor is not None, "next_cursor": cursor}


class FalUsageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output = Path(tmp.name) / "sample.json"
        env = patch.dict(os.environ, {"FAL_BILLING_API_KEY": "test-api-secret",
                                    "FAL_USAGE_OUTPUT": str(self.output),
                                    "FAL_USAGE_TIMEZONE": "America/New_York"})
        env.start()
        self.addCleanup(env.stop)

    def test_exports_discounted_totals_without_account_or_key(self):
        balance = {"username": "private-account", "credits": {
            "current_balance": 15.123456, "currency": "USD"}}
        with patch.object(exporter, "fetch", side_effect=[balance, page(.1, .2), page(2, 3)]):
            exporter.main()
        data = json.loads(self.output.read_text())
        self.assertEqual(data["credit_remaining_usd"], 15.123456)
        self.assertEqual(data["spend_today_usd"], .3)
        self.assertEqual(data["spend_month_to_date_usd"], 5)
        self.assertEqual(data["usage_source"], "estimate")
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-account", self.output.read_text())
        self.assertNotIn("test-api-secret", self.output.read_text())

    def test_paginates_and_leaves_account_scope_unfiltered(self):
        with patch.object(exporter, "fetch", side_effect=[page(.1, cursor="next"), page(.2)]) as fetch:
            now = datetime.now(timezone.utc)
            self.assertEqual(exporter.spending("key", now, now, "UTC"), .3)
        self.assertEqual(fetch.call_args.args[2]["cursor"], "next")
        for call in fetch.call_args_list:
            self.assertNotIn("endpoint_id", call.args[2])
            self.assertNotIn("api_key_id", call.args[2])
            self.assertEqual(call.args[2]["bound_to_timeframe"], "false")

    def test_empty_summary_is_zero_but_missing_summary_is_invalid(self):
        now = datetime.now(timezone.utc)
        with patch.object(exporter, "fetch", return_value=page()):
            self.assertEqual(exporter.spending("key", now, now, "UTC"), 0)
        with patch.object(exporter, "fetch", return_value={"has_more": False, "next_cursor": None}):
            with self.assertRaises(KeyError):
                exporter.spending("key", now, now, "UTC")

    def test_month_and_day_use_local_calendar_even_across_utc_month(self):
        balance = {"credits": {"current_balance": 0, "currency": "USD"}}
        now = datetime(2026, 12, 1, 2, tzinfo=timezone.utc)
        with patch.object(exporter, "fetch", side_effect=[balance, page(), page()]):
            data = exporter.get_usage(now)
        self.assertEqual(data["day_start"], "2026-11-30T00:00:00-05:00")
        # November starts before the DST change; keep the correct offset.
        self.assertEqual(data["month_start"], "2026-11-01T00:00:00-04:00")
        self.assertEqual(data["daily_attributes"]["last_reset"], data["day_start"])
        self.assertEqual(data["monthly_attributes"]["last_reset"], data["month_start"])

    def test_invalid_money_is_rejected(self):
        for value in (True, None, "12", -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                exporter.amount(value)

    def test_repeated_cursor_is_rejected(self):
        now = datetime.now(timezone.utc)
        with patch.object(exporter, "fetch", return_value=page(1, cursor="same")):
            with self.assertRaises(ValueError):
                exporter.spending("key", now, now, "UTC")

    def test_partial_or_failed_poll_preserves_previous_file_and_timestamp(self):
        previous = '{"sampled_at":123,"credit_remaining_usd":5}\n'
        self.output.write_text(previous)
        balance = {"credits": {"current_balance": 0, "currency": "USD"}}
        for failure in (RuntimeError("HTTP 403"), ValueError("malformed response")):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(exporter, "fetch", side_effect=[balance, page(1), failure]):
                    with self.assertRaises(type(failure)):
                        exporter.main()
                self.assertEqual(self.output.read_text(), previous)
                self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_different_currencies_are_not_summed_as_usd(self):
        now = datetime.now(timezone.utc)
        bad = page(10)
        bad["summary"][0]["currency"] = "EUR"
        with patch.object(exporter, "fetch", return_value=bad):
            with self.assertRaises(ValueError):
                exporter.spending("key", now, now, "UTC")

    def test_redirects_never_forward_the_api_key(self):
        handler = exporter.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://example.test"))

    def test_requests_are_authenticated_gets_to_fixed_api_host(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        with patch.object(exporter.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            self.assertEqual(exporter.fetch("account/billing", "test-key", {"expand": "credits"}), {"ok": True})
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(urlsplit(request.full_url).netloc, "api.fal.ai")
        self.assertEqual(request.get_header("Authorization"), "Key test-key")


if __name__ == "__main__":
    unittest.main()
