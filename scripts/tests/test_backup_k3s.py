"""The K3s capture must be complete before a snapshot, or stop the snapshot."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('backup_launcher_k3s', ROOT / 'backup/run.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

# Answers the few kubectl calls the capture makes; FAIL_DUMP breaks pg_dump.
FAKE_KUBECTL = r'''#!/usr/bin/env bash
case "$*" in
  *"get pod"*) echo pod/postgres-1 ;;
  *"get databases"*) printf 'kaneo\nother_app\n' ;;
  *pg_dump*) [[ -z ${FAIL_DUMP:-} ]] || exit 1; echo "dump of ${*: -2:1}" ;;
  *pg_restore*) cat >/dev/null ;;
  *gitrepository*) echo 'refs/heads/main@sha1:abc' ;;
  *) echo "unexpected: $*" >&2; exit 2 ;;
esac
'''


class CaptureScript(unittest.TestCase):
    def capture(self, root, **extra):
        env = {'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
               'K3S_BACKUP_DIR': str(root / 'k3s'), 'K3S_KUBECONFIG': str(root / 'kubeconfig'),
               'INFRA_DIR': str(root / 'repo'), **extra}
        return subprocess.run([str(ROOT / 'backup/infra-backup-k3s')], env=env,
                              capture_output=True, text=True)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = self.root = Path(self.directory.name)
        (root / 'bin').mkdir()
        (root / 'bin/kubectl.upstream').write_text(FAKE_KUBECTL)
        (root / 'bin/kubectl.upstream').chmod(0o755)
        for name in ('public-edge-inputs/kaneo/credentials.env', 'public-edge-keys/age.agekey'):
            path = root / 'repo/local' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('secret')
        (root / 'kubeconfig').write_text('config')
        (root / 'k3s').mkdir()

    def tearDown(self):
        self.directory.cleanup()

    def test_capture_writes_one_complete_set(self):
        result = self.capture(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        latest = self.root / 'k3s/latest'
        self.assertEqual(sorted(p.name for p in (latest / 'databases').iterdir()), ['kaneo.dump', 'other_app.dump'])
        self.assertEqual((latest / 'databases/kaneo.dump').read_text(), 'dump of kaneo\n')
        self.assertEqual((latest / 'inputs/kaneo/credentials.env').read_text(), 'secret')
        self.assertTrue((latest / 'keys/age.agekey').exists())
        self.assertEqual((latest / 'git-revision.txt').read_text(), 'refs/heads/main@sha1:abc\n')
        self.assertEqual(latest.stat().st_mode & 0o777, 0o700)

    def test_failed_dump_keeps_the_previous_capture(self):
        self.assertEqual(self.capture(self.root).returncode, 0)
        result = self.capture(self.root, FAIL_DUMP='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'k3s/latest/databases/kaneo.dump').read_text(), 'dump of kaneo\n')

    def test_interrupted_swap_is_recovered(self):
        self.assertEqual(self.capture(self.root).returncode, 0)
        (self.root / 'k3s/latest').rename(self.root / 'k3s/previous')
        self.assertEqual(self.capture(self.root, FAIL_DUMP='1').returncode, 1)
        self.assertTrue((self.root / 'k3s/latest/databases/kaneo.dump').exists())
        self.assertFalse((self.root / 'k3s/previous').exists())


class LauncherOrder(unittest.TestCase):
    def run_backup(self, env, capture_fails=False):
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
                   'RESTIC_PASSWORD_FILE': str(root / 'password'), **env}
            raw = b'\0'.join((k + '=' + v).encode() for k, v in env.items()) + b'\0'
            calls = []

            def run(command, **kwargs):
                calls.append(Path(command[0]).name if command[0].startswith('/') else command[0])
                if calls[-1] == 'infra-backup-k3s' and capture_fails:
                    raise subprocess.CalledProcessError(1, command)
                if command[:2] == ['python3', str(ROOT / 'backup/infra-backup-declared.py')]:
                    Path(command[-1], 'plan.json').write_text(json.dumps(
                        {'sources': [], 'offline': [], 'quiesce': {}}))

            argv = ['run.py', '--settings', str(root / 'backup.env'), 'backup']
            with patch.object(sys, 'argv', argv), \
                    patch.object(launcher.os, 'geteuid', return_value=1000), \
                    patch.object(launcher.signal, 'signal'), \
                    patch.object(launcher.subprocess, 'check_output', return_value=raw), \
                    patch.object(launcher.subprocess, 'run', side_effect=run):
                error = None
                try:
                    launcher.main()
                except subprocess.CalledProcessError as caught:
                    error = caught
            result = (root / 'state/last-result')
            return calls, error, json.loads(result.read_text()) if result.exists() else None

    def test_capture_runs_before_the_snapshot(self):
        calls, error, result = self.run_backup({'K3S_BACKUP_DIR': '/opt/k3s-backup'})
        self.assertIsNone(error)
        self.assertEqual(calls[0], 'infra-backup-k3s')
        self.assertIn('docker', calls)
        self.assertEqual(result['result'], 'success')

    def test_failed_capture_stops_the_snapshot(self):
        calls, error, result = self.run_backup({'K3S_BACKUP_DIR': '/opt/k3s-backup'}, capture_fails=True)
        self.assertIsNotNone(error)
        self.assertEqual(calls, ['infra-backup-k3s'])
        self.assertEqual(result, {'result': 'failed', 'operation': 'k3s-capture'})

    def test_hosts_without_k3s_skip_the_capture(self):
        calls, error, _ = self.run_backup({})
        self.assertIsNone(error)
        self.assertNotIn('infra-backup-k3s', calls)


if __name__ == '__main__':
    unittest.main()
