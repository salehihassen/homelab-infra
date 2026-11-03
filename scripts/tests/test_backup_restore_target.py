"""A user-mode restore must write only to an explicitly selected scratch mount."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('backup_launcher', ROOT / 'backup/run.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class RestoreTargetTests(unittest.TestCase):
    def test_user_restore_mounts_selected_private_target_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'scratch'
            config = root / 'tool.json'
            config.write_text(json.dumps({
                'tool_image': 'python:example', 'docker_binary': '/usr/bin/docker',
                'compose_binary': '/usr/bin/compose', 'restic_binary': '/usr/bin/restic',
                'rclone_binary': '/usr/bin/rclone'}))
            env = {'INFRA_DIR': str(ROOT), 'BACKUP_TOOL_CONFIG': str(config),
                   'BACKUP_STATE_DIR': str(root / 'state'), 'RESTIC_CACHE_DIR': str(root / 'cache'),
                   'RCLONE_CONFIG': str(root / 'rclone.conf'),
                   'RESTIC_PASSWORD_FILE': str(root / 'password')}
            raw = b'\0'.join((k + '=' + v).encode() for k, v in env.items()) + b'\0'
            argv = ['run.py', '--settings', str(root / 'backup.env'), 'restic',
                    'restore', 'example-snapshot', '--target', str(target), '--verify']
            with patch.object(sys, 'argv', argv), \
                    patch.object(launcher.os, 'geteuid', return_value=1000), \
                    patch.object(launcher.signal, 'signal'), \
                    patch.object(launcher.subprocess, 'check_output', return_value=raw), \
                    patch.object(launcher.subprocess, 'run') as run:
                launcher.main()
            command = run.call_args_list[0].args[0]
            self.assertIn('type=bind,src=' + str(target) + ',dst=' + str(target), command)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertIn('--verify', command)

    def test_nonempty_target_is_rejected_without_starting_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'keep').write_text('existing data')
            argv = ['run.py', 'restic', 'restore', 'example-snapshot', '--target', str(target)]
            with patch.object(sys, 'argv', argv), \
                    patch.object(launcher.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'must be empty'):
                    launcher.main()
            run.assert_not_called()
            self.assertEqual((target / 'keep').read_text(), 'existing data')
