"""Shared capture failures must resume writers and retain evidence."""
import importlib.util
import json
from pathlib import Path
import subprocess
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
    def test_nested_copy_roots_preserve_the_original_directory_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'app'
            (source / 'data').mkdir(parents=True)
            (source / 'data/file').write_text('saved data')
            stage = root / 'stage'
            stage.mkdir()
            declarations = {'app': {'paths': [str(source)], 'quiesce': {
                'services': ['writer'], 'method': 'stop', 'copy': [str(source)]}}}
            config = {'name': 'server', 'services': {'writer': {
                'image': 'example:1', 'volumes': [{'type': 'bind', 'source': str(source / 'data')}]}}}
            with patch.object(backup, 'check', return_value=(root, declarations)), \
                    patch.object(backup.subprocess, 'check_output', return_value=json.dumps(config)):
                backup.plan(root, 'server-2', stage)
            planned = json.loads((stage / 'plan.json').read_text())
            self.assertEqual(planned['quiesce']['app']['copy'], [str(source)])
            run = subprocess.run
            with patch.object(backup, 'check_space'), \
                    patch.object(backup.subprocess, 'check_output', return_value='writer'), \
                    patch.object(backup, 'docker'), \
                    patch.object(backup.subprocess, 'run', side_effect=lambda args, **kw:
                                 SimpleNamespace(returncode=0) if args[0] == 'docker' else run(args, **kw)):
                backup.dump_postgres(stage)
            recovered = stage / 'quiesced' / source.relative_to('/')
            self.assertEqual((recovered / 'data/file').read_text(), 'saved data')
            self.assertFalse((recovered / 'data/data').exists())

    def test_interrupted_maintenance_enable_is_cleaned_up(self):
        for error in (InterruptedError('cancelled'), subprocess.CalledProcessError(1, ['docker', 'exec'])):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory)
                (stage / 'plan.json').write_text(json.dumps({'postgres': {}, 'quiesce': {
                    'app': {'services': ['writer'], 'method': 'stop', 'copy': []}}, 'adapters': {'app': {}}}))
                maintenance = {'on': False}
                def enable(*args, **kwargs):
                    maintenance['on'] = True
                    raise error
                with patch.object(backup, 'check_space'), \
                        patch.object(backup.subprocess, 'check_output', return_value='writer'), \
                        patch.object(nextcloud, 'output', return_value='{"maintenance": false}'), \
                        patch.object(nextcloud, 'command', side_effect=enable), \
                        patch.object(nextcloud, 'resume', side_effect=lambda: maintenance.update(on=False)) as resume, \
                        patch.object(backup, 'docker') as docker:
                    with self.assertRaises(type(error)):
                        backup.dump_postgres(stage)
                    resume.assert_called_once()
                    docker.assert_not_called()
                    self.assertFalse(maintenance['on'])

    def run_failure(self, adapter=None):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            plan = {'postgres': {}, 'quiesce': {'app': {'services': ['writer'], 'method': 'stop', 'copy': ['/data']}}, 'adapters': {'app': {}} if adapter else {}}
            (stage / 'plan.json').write_text(json.dumps(plan))
            calls = []
            with patch.object(backup, 'check_space'), patch.object(backup.subprocess, 'check_output', return_value='writer\n'), patch.object(backup.subprocess, 'run', side_effect=lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0)), patch.object(nextcloud, 'prepare', side_effect=lambda cleanup: cleanup.callback(nextcloud.resume)) as prepare, patch.object(nextcloud, 'resume') as resume, patch.object(nextcloud, 'capture', side_effect=RuntimeError('export failed')) as capture:
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
            with patch.object(backup, 'check_space'), patch.object(backup.subprocess, 'check_output', return_value='writer\n'), patch.object(nextcloud, 'output', return_value='{"maintenance": true}'), patch.object(nextcloud, 'resume') as resume, patch.object(backup, 'docker') as docker:
                with self.assertRaisesRegex(RuntimeError, 'existing Nextcloud maintenance'):
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
