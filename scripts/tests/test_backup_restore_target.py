"""A user-mode restore must write only to an explicitly selected scratch mount."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('backup_launcher', ROOT / 'backup/run.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class RestoreTargetTests(unittest.TestCase):
    def test_overlapping_helper_interruption_leaves_other_invocation_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
            active, names, handlers = set(), [], []

            def docker(command, **kwargs):
                if command[:2] == ['docker', 'run']:
                    name = command[command.index('--name') + 1]
                    if name in active:
                        raise subprocess.CalledProcessError(125, command)
                    active.add(name)
                    names.append(name)
                    if len(names) == 1:
                        with self.assertRaises(InterruptedError):
                            launcher.main()
                        self.assertEqual(active, {name})
                    else:
                        handlers[-1](launcher.signal.SIGTERM, None)
                elif command[:2] in (['docker', 'stop'], ['docker', 'rm']):
                    active.discard(command[-1])

            argv = ['run.py', '--settings', str(root / 'backup.env'), 'restic', 'snapshots']
            with patch.object(sys, 'argv', argv), \
                    patch.object(launcher.os, 'geteuid', return_value=1000), \
                    patch.object(launcher.signal, 'signal', side_effect=lambda sig, fn: handlers.append(fn)), \
                    patch.object(launcher.subprocess, 'check_output', return_value=raw), \
                    patch.object(launcher.subprocess, 'run', side_effect=docker):
                launcher.main()
            self.assertEqual(len(set(names)), 2)
            self.assertEqual(active, set())

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
                    patch.object(launcher.os, 'geteuid', return_value=root.stat().st_uid), \
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

    def test_existing_public_target_is_rejected_before_reading_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            target.chmod(0o755)
            argv = ['run.py', 'restic', 'restore', 'example-snapshot', '--target', str(target)]
            with patch.object(sys, 'argv', argv), \
                    patch.object(launcher.subprocess, 'check_output') as output:
                with self.assertRaisesRegex(RuntimeError, 'mode 0700'):
                    launcher.main()
            output.assert_not_called()
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_target_owned_by_another_user_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            argv = ['run.py', 'restic', 'restore', 'example-snapshot', '--target', str(target)]
            with patch.object(sys, 'argv', argv), \
                    patch.object(launcher.os, 'geteuid', return_value=target.stat().st_uid + 1), \
                    patch.object(launcher.subprocess, 'check_output') as output:
                with self.assertRaisesRegex(RuntimeError, 'owned by the current user'):
                    launcher.main()
            output.assert_not_called()
