import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("exporter", Path(__file__).with_name("export-condenseit-digest.py"))
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class DigestTests(unittest.TestCase):
    def test_compact_sample_preserves_order_and_links_saved_digest(self):
        payload = {"meta": {"id": 18, "created_at": "2026-10-13T11:00:00Z"},
                   "items": [{"title": f"Story {i}", "source": "Feed", "summary": "large", "private": "omit"} for i in range(8)]}
        sample = exporter.make_sample(payload, "https://news.private.example", 123)
        self.assertEqual(sample["item_count"], 5)
        self.assertEqual(sample["items"], [{"title": f"Story {i}", "source": "Feed"} for i in range(5)])
        self.assertEqual(sample["digest_url"], "https://news.private.example/?id=18")
        self.assertNotIn("large", json.dumps(sample))

    def test_no_digest_yet(self):
        sample = exporter.make_sample(None, "https://news.private.example", 123)
        self.assertEqual(sample["items"], [])
        self.assertEqual(sample["digest_id"], 0)

    def test_malformed_response_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sample.json"
            exporter.write_sample(output, {"digest_id": 17})
            with self.assertRaises(ValueError):
                sample = exporter.make_sample({"meta": {"id": 18, "created_at": "today"}, "items": [{"title": None}]}, "https://news.private.example", 123)
                exporter.write_sample(output, sample)
            self.assertEqual(json.loads(output.read_text()), {"digest_id": 17})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_redirects_do_not_forward_authorization(self):
        self.assertIsNone(exporter.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.private.example"))


if __name__ == "__main__":
    unittest.main()
