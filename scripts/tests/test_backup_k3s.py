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

# Answers the few kubectl calls the capture makes; FAIL_DUMP breaks pg_dump and
# DELETE_AFTER_DUMP removes an object as soon as the dump is taken.
FAKE_KUBECTL = r'''#!/usr/bin/env bash
case "$*" in
  *"get pod"*) echo pod/postgres-1 ;;
  *"get databases"*) printf 'kaneo\nother_app\n' ;;
  *pg_dump*) [[ -z ${FAIL_DUMP:-} ]] || exit 1; echo "dump of ${*: -2:1}"
    [[ -z ${DELETE_AFTER_DUMP:-} ]] || rm -f "${DELETE_AFTER_DUMP}" ;;
  *pg_restore*)
    if [[ -n ${CHECK_DUMP_DRAINED:-} ]]; then
      while [[ $1 != -- ]]; do shift; done
      shift
      status=0
      "$@" || status=$?
      if IFS= read -r -n 1; then
        echo 'archive command left unread input' >&2
        exit 97
      fi
      exit "$status"
    fi
    cat >/dev/null ;;
  *gitrepository*) echo 'refs/heads/main@sha1:abc' ;;
  *"service garage"*) echo 10.145.0.9 ;;
  *) echo "unexpected: $*" >&2; exit 2 ;;
esac
'''

# Lists one bucket, the FAKE_GARAGE directory, and mirrors it; records how it
# received the key. FAIL_SYNC breaks the mirror.
FAKE_RCLONE = r'''#!/usr/bin/env bash
echo "args: $* key: ${RCLONE_CONFIG_GARAGE_SECRET_ACCESS_KEY:-} at: ${RCLONE_CONFIG_GARAGE_ENDPOINT:-}" >>"${RCLONE_LOG}"
case "$*" in
  *lsf*) echo kaneo-uploads/ ;;
  *sync*) [[ -z ${FAIL_SYNC:-} ]] || exit 1
    rm -rf "${*: -1}"; cp -a "${FAKE_GARAGE}" "${*: -1}" ;;
  *copy*) cp -a "${FAKE_GARAGE}/." "${*: -1}/" ;;
esac
'''


class CaptureScript(unittest.TestCase):
    def capture(self, root, **extra):
        env = {'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
               'K3S_BACKUP_DIR': str(root / 'k3s'), 'K3S_KUBECONFIG': str(root / 'kubeconfig'),
               'INFRA_DIR': str(root / 'repo'), 'BACKUP_TOOL_CONFIG': str(root / 'tool.json'),
               'RCLONE_LOG': str(root / 'rclone.log'), 'FAKE_GARAGE': str(root / 'garage'), **extra}
        return subprocess.run([str(ROOT / 'backup/infra-backup-k3s')], env=env,
                              capture_output=True, text=True)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = self.root = Path(self.directory.name)
        (root / 'bin').mkdir()
        (root / 'bin/kubectl.upstream').write_text(FAKE_KUBECTL)
        (root / 'bin/kubectl.upstream').chmod(0o755)
        (root / 'bin/pg_restore').write_text(
            '#!/usr/bin/env python3\nimport os, sys\nos.read(0, 1)\n'
            'sys.exit(int(os.environ.get("FAIL_RESTORE", "0")))\n')
        (root / 'bin/pg_restore').chmod(0o755)
        for name in ('public-edge-inputs/kaneo/credentials.env', 'public-edge-keys/age.agekey'):
            path = root / 'repo/local' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('secret')
        (root / 'kubeconfig').write_text('config')
        (root / 'k3s').mkdir()
        (root / 'bin/rclone').write_text(FAKE_RCLONE)
        (root / 'bin/rclone').chmod(0o755)
        (root / 'tool.json').write_text(json.dumps({'rclone_binary': str(root / 'bin/rclone')}))
        (root / 'garage/workspace').mkdir(parents=True)
        (root / 'garage/workspace/file.png').write_text('object\n')

    def add_storage_key(self):
        (self.root / 'repo/local/public-edge-inputs/kaneo/storage.env').write_text(
            'S3_ACCESS_KEY_ID=GKexample\nS3_SECRET_ACCESS_KEY=storage-secret\n')

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

    def test_archive_validation_drains_remaining_input(self):
        result = self.capture(self.root, CHECK_DUMP_DRAINED='1')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_archive_validation_keeps_the_previous_capture(self):
        self.assertEqual(self.capture(self.root).returncode, 0)
        result = self.capture(self.root, CHECK_DUMP_DRAINED='1', FAIL_RESTORE='5')
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual((self.root / 'k3s/latest/databases/kaneo.dump').read_text(), 'dump of kaneo\n')

    def test_buckets_are_mirrored_into_the_set(self):
        self.add_storage_key()
        result = self.capture(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        copy = self.root / 'k3s/latest/objects/kaneo-uploads/workspace/file.png'
        self.assertEqual(copy.read_text(), 'object\n')
        # A hard link to the persistent mirror, not a second copy.
        self.assertEqual(copy.stat().st_ino,
                         (self.root / 'k3s/objects/kaneo-uploads/workspace/file.png').stat().st_ino)
        log = (self.root / 'rclone.log').read_text()
        self.assertIn('key: storage-secret at: http://10.145.0.9:3900', log)
        self.assertNotIn('storage-secret', ''.join(line.split(' key: ')[0] for line in log.splitlines()))

    def test_objects_deleted_after_the_dump_stay_in_the_set(self):
        self.add_storage_key()
        self.assertEqual(self.capture(self.root).returncode, 0)
        (self.root / 'garage/workspace/new.png').write_text('new\n')
        result = self.capture(self.root, DELETE_AFTER_DUMP=str(self.root / 'garage/workspace/new.png'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'k3s/latest/objects/kaneo-uploads/workspace/new.png').read_text(), 'new\n')
        # The next run drops it.
        self.assertEqual(self.capture(self.root).returncode, 0)
        self.assertFalse((self.root / 'k3s/latest/objects/kaneo-uploads/workspace/new.png').exists())

    def test_failed_mirror_keeps_the_previous_capture(self):
        self.add_storage_key()
        self.assertEqual(self.capture(self.root).returncode, 0)
        result = self.capture(self.root, FAIL_SYNC='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / 'k3s/latest/objects/kaneo-uploads/workspace/file.png').exists())

    def test_apps_without_storage_skip_the_mirror(self):
        self.assertEqual(self.capture(self.root).returncode, 0)
        self.assertFalse((self.root / 'rclone.log').exists())
        self.assertFalse((self.root / 'k3s/latest/objects').exists())

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
