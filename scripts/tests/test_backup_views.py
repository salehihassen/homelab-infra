"""Read-only management views must not prevent cold backups of retired state."""
import importlib.util
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "extra_state", REPO / "hosts/server-1/backup/infra-backup-extra-state.py")
extra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extra)


class BackupViewTests(unittest.TestCase):
    def test_locally_built_image_with_containerd_digest_is_archived(self):
        self.assertTrue(extra.needs_image_archive(
            'local/bridge:1', {'RepoDigests': ['local/bridge@sha256:abc']}, ['local/bridge:1']))
        self.assertFalse(extra.needs_image_archive(
            'registry/app:1', {'RepoDigests': ['registry/app@sha256:abc']}, ['local/bridge:1']))

    def test_declared_read_only_observer_is_not_a_writer(self):
        containers = [{"Name": "/komodo-periphery", "Mounts": [
            {"Source": "/opt", "RW": False},
            {"Source": "/opt/komodo/periphery", "RW": True}]}]
        self.assertEqual(extra.in_use("/opt/postgresql/data", containers, ["/opt"]), [])

    def test_actual_writer_still_blocks_a_cold_archive(self):
        containers = [{"Name": "/database", "Mounts": [
            {"Source": "/opt/postgresql/data", "RW": True}]}]
        self.assertEqual(extra.in_use("/opt/postgresql/data", containers, ["/opt"]), ["database"])

    def test_undeclared_or_writable_broad_mount_still_blocks(self):
        for writable, views in [(False, []), (True, ["/opt"])]:
            containers = [{"Name": "/consumer", "Mounts": [{"Source": "/opt", "RW": writable}]}]
            self.assertEqual(extra.in_use("/opt/postgresql/data", containers, views), ["consumer"])
