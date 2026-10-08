"""Cover API format changes and authenticated monthly quota extraction."""

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "ollama_usage", Path(__file__).with_name("export-ollama-usage.py")
)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

SETTINGS = """
<h2><span>Included usage</span><span>free</span></h2>
<section>
  <div><span>Monthly usage</span><span>$0.15 of $5 used</span
  ></div>
  <div data-usage-track><div style="width: 3%;"></div></div>
  <div data-time="2026-10-25T12:00:00Z">Resets in 3 weeks.</div>
</section>
<section><span>Weekly usage</span><span>99% used</span>
<div data-time="2026-10-08T00:00:00Z">Resets tomorrow</div></section>
"""
ACTIVITY = {"range": "7d", "scope": "self", "totals": {"request_count": 71}, "buckets": []}
ACCOUNT = {"Plan": "free", "CreatedAt": "2026-01-25T12:00:00Z", "Email": "private@example.test"}


class OllamaExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "usage.json"
        self.cookie = self.root / "cookie"
        self.cookie.write_text("wos-session=test-session-secret")
        self.env = patch.dict(os.environ, {
            "OLLAMA_CLOUD_API_KEY": "test-api-secret",
            "OLLAMA_USAGE_COOKIE_FILE": str(self.cookie),
            "OLLAMA_USAGE_BROWSER_COOKIES_FILE": "",
            "OLLAMA_USAGE_OUTPUT": str(self.output),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_changed_activity_api_uses_settings_quota_not_request_totals(self):
        with patch.object(exporter, "fetch", side_effect=[ACTIVITY, ACCOUNT]), \
                patch.object(exporter, "fetch_settings", return_value=SETTINGS, create=True):
            exporter.main()
        data = json.loads(self.output.read_text())
        self.assertEqual(data["monthly_used"], 3.0)
        self.assertEqual(data["monthly_remaining"], 97.0)
        self.assertEqual(data["monthly_resets_at"], 1792929600)
        # Seven-day activity cannot stand in for the billing-cycle request count.
        self.assertIsNone(data["monthly_requests"])
        self.assertEqual(data["plan"], "free")
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        for secret in ("test-api-secret", "test-session-secret", ACCOUNT["Email"]):
            self.assertNotIn(secret, self.output.read_text())

    def test_legacy_api_still_exports_monthly_usage_and_requests(self):
        payload = {"limits": {"monthly": {"usage": 0.029,
                    "models": [{"request_count": 123}]}}}
        with patch.object(exporter, "fetch", side_effect=[payload, ACCOUNT]), \
                patch.object(exporter, "fetch_settings") as settings:
            data = exporter.get_usage()
        self.assertEqual(data["monthly_remaining"], 97.1)
        self.assertEqual(data["monthly_requests"], 123)
        settings.assert_not_called()

    def test_current_settings_use_monthly_reset_and_clamp_exhausted_credit(self):
        used, reset = exporter.parse_monthly_settings(SETTINGS.replace("$0.15 of $5", "$7 of $5"))
        self.assertEqual(used, 100)
        self.assertEqual(reset, 1792929600)

    def test_free_plan_percentage_meter_and_current_session_cookie(self):
        html = SETTINGS.replace("Monthly usage", "Free usage").replace("$0.15 of $5 used", "2.9% used")
        self.assertEqual(exporter.parse_monthly_settings(html), (2.9, 1792929600))

    def test_imported_session_survives_browser_lock_and_saves_server_rotation(self):
        db = self.root / "cookies.sqlite"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE moz_cookies(host TEXT,path TEXT,name TEXT,value TEXT,expiry INTEGER)")
        connection.execute("INSERT INTO moz_cookies VALUES('.ollama.com','/','__Secure-session','imported-session',9999999999)")
        connection.commit()
        self.addCleanup(connection.close)
        self.cookie.unlink()
        os.environ["OLLAMA_USAGE_BROWSER_COOKIES_FILE"] = str(db)
        headers = Message()
        headers.add_header("Set-Cookie", "__Secure-session=renewed-session; Path=/; Secure; HttpOnly")
        headers.add_header("Set-Cookie", "tracking=ignore-this; Path=/")
        response = BytesIO(SETTINGS.encode())
        response.headers = headers
        with patch.object(exporter.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            exporter.fetch_settings()
        self.assertEqual(self.cookie.read_text().strip(), "__Secure-session=renewed-session")
        self.assertEqual(self.cookie.stat().st_mode & 0o777, 0o600)
        connection.execute("BEGIN EXCLUSIVE")
        self.assertEqual(exporter.session_cookie(), "__Secure-session=renewed-session")
        connection.rollback()

    def test_invalid_settings_preserve_last_good_sample(self):
        self.output.write_text('{"sampled_at":1,"monthly_remaining":80}')
        original = self.output.read_bytes()
        for html in ("<html>Sign in</html>", SETTINGS.replace("$5 used", "$0 used"),
                     SETTINGS.replace("2026-10-25T12:00:00Z", "invalid"),
                     SETTINGS.replace("$0.15 of $5 used", "$bad of $5 used")):
            with self.subTest(html=html), \
                    patch.object(exporter, "fetch", side_effect=[ACTIVITY, ACCOUNT]), \
                    patch.object(exporter, "fetch_settings", return_value=html):
                with self.assertRaises(ValueError):
                    exporter.main()
                self.assertEqual(self.output.read_bytes(), original)

    def test_missing_session_is_actionable_without_showing_secrets(self):
        self.cookie.unlink()
        with patch.object(exporter, "fetch", side_effect=[ACTIVITY, ACCOUNT]):
            with self.assertRaisesRegex(RuntimeError, "Ollama.*session"):
                exporter.get_usage()

    def test_expired_browser_session_never_reaches_network(self):
        db = self.root / "cookies.sqlite"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE moz_cookies(host TEXT,path TEXT,name TEXT,value TEXT,expiry INTEGER)")
        connection.execute("INSERT INTO moz_cookies VALUES('.ollama.com','/','wos-session','expired-secret',1)")
        connection.commit()
        connection.close()
        os.environ["OLLAMA_USAGE_COOKIE_FILE"] = ""
        os.environ["OLLAMA_USAGE_BROWSER_COOKIES_FILE"] = str(db)
        with patch.object(exporter.urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(RuntimeError, "Ollama.*session"):
                exporter.fetch_settings()
            opener.assert_not_called()

    def test_browser_cookie_read_is_scoped_read_only_and_sees_session_rotation(self):
        db = self.root / "cookies.sqlite"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE moz_cookies(host TEXT,path TEXT,name TEXT,value TEXT,expiry INTEGER)")
        connection.executemany("INSERT INTO moz_cookies VALUES(?,?,?,?,?)", [
            (".ollama.com", "/", "wos-session", "first-session", 9999999999),
            (".ollama.com", "/", "tracking-cookie", "unrelated-secret", 9999999999),
            ("other.example", "/", "wos-session", "other-site-secret", 9999999999),
        ])
        connection.commit()
        os.environ["OLLAMA_USAGE_COOKIE_FILE"] = ""
        os.environ["OLLAMA_USAGE_BROWSER_COOKIES_FILE"] = str(db)
        original = db.read_bytes()
        self.assertEqual(exporter.session_cookie(), "wos-session=first-session")
        self.assertEqual(db.read_bytes(), original)
        connection.execute("UPDATE moz_cookies SET value='rotated-session' WHERE host='.ollama.com' AND name='wos-session'")
        connection.commit()
        connection.close()
        self.assertEqual(exporter.session_cookie(), "wos-session=rotated-session")

    def test_settings_redirect_rejects_expired_session_without_following_it(self):
        error = urllib.error.HTTPError("https://ollama.com/settings", 303, "redirect", {}, None)
        with patch.object(exporter.urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = error
            with self.assertRaisesRegex(RuntimeError, "Ollama.*session"):
                exporter.fetch_settings()
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("Cookie"), "wos-session=test-session-secret")


if __name__ == "__main__":
    unittest.main()
