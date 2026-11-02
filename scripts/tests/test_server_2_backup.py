"""Failure paths must restore application availability and retain evidence."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


path = Path(__file__).resolve().parents[2] / "hosts/server-2/backup/infra-backup.py"
spec = importlib.util.spec_from_file_location("server_2_backup", path)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class FailedCapture(unittest.TestCase):
    def test_database_export_failure_resumes_nextcloud_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            names = ["nextcloud-aio-mastercontainer", "nextcloud-aio-nextcloud", "nextcloud-aio-database", "nextcloud-aio-redis"]
            group = {"containers": [{"Name": "/" + n, "Image": "sha256:example"} for n in names]}
            with patch.object(backup, "output", side_effect=["[]", '{"maintenance":false}']), patch.object(backup, "command", side_effect=lambda args, **kw: calls.append(args)), patch.object(backup, "nextcloud_exports", side_effect=RuntimeError("simulated database export failure")), patch.object(backup.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as cleanup:
                with self.assertRaisesRegex(RuntimeError, "database export failure"):
                    backup.backup_stack({"state_dir": directory}, "nextcloud-aio", group)
            for name in names[:2]:
                self.assertIn(["docker", "stop", "--time", "60", name], calls)
                self.assertIn(["docker", "start", name], calls)
            for name in names[2:]:
                self.assertNotIn(["docker", "stop", "--time", "60", name], calls)
            self.assertIn("--off", cleanup.call_args.args[0])
            self.assertTrue((Path(directory) / "staging/nextcloud-aio/inventory.json").is_file())

    def test_existing_nextcloud_maintenance_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            group = {"containers": [{"Name": "/nextcloud-aio-nextcloud", "Image": "sha256:example"}]}
            with patch.object(backup, "output", side_effect=["[]", '{"maintenance":true}']), patch.object(backup, "command") as command, patch.object(backup.subprocess, "run") as cleanup:
                with self.assertRaisesRegex(RuntimeError, "Preserving existing"):
                    backup.backup_stack({"state_dir": directory}, "nextcloud-aio", group)
                command.assert_not_called()
                cleanup.assert_not_called()

    def test_upload_failure_restarts_writer_and_retains_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            group = {"containers": [{"Name": "/flowise", "Image": "sha256:example"}], "paths": [], "mounts": {}, "exclude": []}
            with patch.object(backup, "output", return_value="[]"), patch.object(backup, "command", side_effect=lambda args, **kw: calls.append(args)), patch.object(backup, "helper", side_effect=OSError("simulated destination outage")), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(OSError):
                    backup.backup_stack({"state_dir": directory}, "flowise", group)
            self.assertIn(["docker", "stop", "--time", "60", "flowise"], calls)
            self.assertIn(["docker", "start", "flowise"], calls)
            self.assertTrue((Path(directory) / "staging/flowise/inventory.json").is_file())

    def test_stale_capture_fails_before_stopping_a_service(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "staging/flowise").mkdir(parents=True)
            with patch.object(backup, "command") as command:
                with self.assertRaisesRegex(RuntimeError, "stale staging"):
                    backup.backup_stack({"state_dir": directory}, "flowise", {})
                command.assert_not_called()
