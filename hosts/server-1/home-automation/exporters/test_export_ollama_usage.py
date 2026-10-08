"""Cover the API-key-only export of Ollama's included monthly credit."""

import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "ollama_usage", Path(__file__).with_name("export-ollama-usage.py")
)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

BALANCE = {
    "included": {"balance_usd": 2.42106, "allowance_usd": 2.5,
                 "period": {"from": "2026-09-26T05:36:52.170186Z",
                            "until": "2026-10-26T05:36:52.170186Z"}},
    "purchased": {"balance_usd": 0},
}
USAGE = {"range": "30d", "granularity": "day", "totals": {"request_count": 120}, "buckets": [
    # Ends before the billing period starts: not counted.
    {"from": "2026-09-24T00:00:00Z", "until": "2026-09-25T00:00:00Z", "request_count": 40},
    # Overlaps the period start: counted (daily buckets cannot be split).
    {"from": "2026-09-26T00:00:00Z", "until": "2026-09-27T00:00:00Z", "request_count": 5},
    {"from": "2026-10-07T00:00:00Z", "until": "2026-10-08T00:00:00Z", "request_count": 70,
     "usage_usd": 0.003},
    {"from": "2026-10-08T00:00:00Z", "until": "2026-10-08T15:00:00Z", "request_count": 5,
     "partial": True},
]}
ACCOUNT = {"Plan": "free", "CreatedAt": "2026-01-26T05:36:52Z", "Email": "private@example.test"}
RESET = 1792993012


def responses(balance=BALANCE, usage=USAGE, account=ACCOUNT):
    """Answer each endpoint by URL, so call order does not matter."""
    def fetch(url, api_key, method="GET"):
        if url.startswith(exporter.BALANCE_URL):
            return balance
        if url.startswith(exporter.USAGE_URL):
            return usage
        if url.startswith(exporter.ACCOUNT_URL):
            return account
        raise AssertionError(url)
    return fetch


class OllamaExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "usage.json"
        self.env = patch.dict(os.environ, {
            "OLLAMA_CLOUD_API_KEY": "test-api-secret",
            "OLLAMA_USAGE_OUTPUT": str(self.output),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_exports_included_credit_period_and_requests(self):
        with patch.object(exporter, "fetch", side_effect=responses()):
            exporter.main()
        data = json.loads(self.output.read_text())
        # (2.5 - 2.42106) / 2.5 = 3.16% used.
        self.assertEqual(data["monthly_used"], 3.2)
        self.assertEqual(data["monthly_remaining"], 96.8)
        self.assertEqual(data["monthly_resets_at"], RESET)
        self.assertEqual(data["monthly_requests"], 80)
        self.assertEqual(data["plan"], "free")
        self.assertEqual(data["included_balance_usd"], 2.42106)
        self.assertEqual(data["included_allowance_usd"], 2.5)
        self.assertEqual(data["purchased_balance_usd"], 0)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        for secret in ("test-api-secret", ACCOUNT["Email"]):
            self.assertNotIn(secret, self.output.read_text())

    def test_overspent_and_unused_credit_are_clamped(self):
        for balance, used in ((-1.0, 100.0), (3.0, 0.0)):
            with self.subTest(balance=balance):
                payload = json.loads(json.dumps(BALANCE))
                payload["included"]["balance_usd"] = balance
                with patch.object(exporter, "fetch", side_effect=responses(balance=payload)):
                    self.assertEqual(exporter.get_usage()["monthly_used"], used)

    def test_missing_request_counts_do_not_block_the_quota(self):
        with patch.object(exporter, "fetch", side_effect=responses(usage={"buckets": "bad"})):
            self.assertIsNone(exporter.get_usage()["monthly_requests"])

    def test_invalid_balance_preserves_last_good_sample(self):
        self.output.write_text('{"sampled_at":1,"monthly_remaining":80}')
        original = self.output.read_bytes()
        broken = []
        for path, value in ((("included", "allowance_usd"), 0),
                            (("included", "allowance_usd"), float("nan")),
                            (("included", "balance_usd"), "2.4"),
                            (("included", "period", "until"), "not-a-time"),
                            (("included", "period", "until"), "2026-10-26T05:36:52")):
            payload = json.loads(json.dumps(BALANCE))
            target = payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            broken.append(payload)
        broken.append({"purchased": {"balance_usd": 0}})
        for payload in broken:
            with self.subTest(payload=payload), \
                    patch.object(exporter, "fetch", side_effect=responses(balance=payload)):
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    exporter.main()
                self.assertEqual(self.output.read_bytes(), original)

    def test_http_errors_are_reported_without_the_api_key(self):
        error = urllib.error.HTTPError(exporter.BALANCE_URL, 401, "unauthorized", {}, None)
        with patch.object(exporter.urllib.request, "urlopen", side_effect=error) as opener:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                exporter.fetch(exporter.BALANCE_URL, "test-api-secret")
        self.assertNotIn("test-api-secret", str(raised.exception))
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-api-secret")

    def test_missing_api_key_is_actionable(self):
        del os.environ["OLLAMA_CLOUD_API_KEY"]
        with self.assertRaisesRegex(ValueError, "OLLAMA_CLOUD_API_KEY"):
            exporter.get_usage()


if __name__ == "__main__":
    unittest.main()
