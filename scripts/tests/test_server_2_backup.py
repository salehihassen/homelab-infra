"""Shared capture failures must resume writers and retain evidence."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2] / 'backup'
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('declared_shared', ROOT / 'infra-backup-declared.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
import nextcloud


class CoordinatedCapture(unittest.TestCase):
    def run_failure(self, adapter=None):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            plan = {'postgres': {}, 'quiesce': {'app': {'services': ['writer'], 'method': 'stop', 'copy': ['/data']}}, 'adapters': {'app': {}} if adapter else {}}
            (stage / 'plan.json').write_text(json.dumps(plan))
            calls = []
            with patch.object(backup, 'check_space'), patch.object(backup.subprocess, 'check_output', return_value='writer\n'), patch.object(backup.subprocess, 'run', side_effect=lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0)), patch.object(nextcloud, 'prepare') as prepare, patch.object(nextcloud, 'resume') as resume, patch.object(nextcloud, 'capture', side_effect=RuntimeError('export failed')) as capture:
                if adapter:
                    with self.assertRaisesRegex(RuntimeError, 'export failed'):
                        backup.dump_postgres(stage)
                    prepare.assert_called_once()
                    resume.assert_called_once()
                else:
                    def fail_copy(args, **kw):
                        calls.append(args)
                        if args[0] == 'cp':
                            raise OSError('copy failed')
                        return SimpleNamespace(returncode=0)
                    with patch.object(backup.subprocess, 'run', side_effect=fail_copy):
                        with self.assertRaisesRegex(OSError, 'copy failed'):
                            backup.dump_postgres(stage)
                self.assertIn(['docker', 'stop', 'writer'], calls)
                self.assertIn(['docker', 'start', 'writer'], calls)
                self.assertTrue((stage / 'plan.json').exists())

    def test_copy_failure_resumes_writer(self):
        self.run_failure()

    def test_native_export_failure_resumes_writer_and_clears_maintenance(self):
        self.run_failure(adapter=True)

    def test_existing_maintenance_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / 'plan.json').write_text(json.dumps({'postgres': {}, 'quiesce': {'app': {'services': ['writer'], 'method': 'stop', 'copy': ['/data']}}, 'adapters': {'app': {}}}))
            with patch.object(backup, 'check_space'), patch.object(backup.subprocess, 'check_output', return_value='writer\n'), patch.object(nextcloud, 'prepare', side_effect=RuntimeError('existing maintenance')), patch.object(nextcloud, 'resume') as resume, patch.object(backup, 'docker') as docker:
                with self.assertRaisesRegex(RuntimeError, 'existing maintenance'):
                    backup.dump_postgres(stage)
                resume.assert_not_called()
                docker.assert_not_called()

    def test_new_child_volume_is_rejected(self):
        names = ['mastercontainer', 'nextcloud', 'database', 'redis']
        children = [{'Name': '/nextcloud-aio-' + name, 'Mounts': [{'Type': 'volume', 'Name': 'unknown', 'Source': '/volumes/unknown'}]} for name in names]
        with patch.object(nextcloud, 'output', side_effect=['id', json.dumps(children)]):
            with self.assertRaisesRegex(RuntimeError, 'Undeclared AIO volume'):
                nextcloud.inspect({'app': {'volumes': []}})

    def test_insufficient_space_fails_before_stopping_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / 'plan.json').write_text(json.dumps({'postgres': {}, 'quiesce': {'app': {'services': ['writer'], 'copy': ['/data']}}}))
            with patch.object(backup.subprocess, 'check_output', return_value='100 /data'), patch.object(backup.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)), patch.object(backup, 'docker') as docker:
                with self.assertRaisesRegex(RuntimeError, 'Insufficient free space'):
                    backup.dump_postgres(stage)
                docker.assert_not_called()
