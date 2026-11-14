#!/usr/bin/env python3
"""One backup interface; Docker supplies root file access on user-service hosts."""
import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import uuid

import yaml

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', default=os.environ.get('BACKUP_ENV', '/etc/infra-backup/backup.env'))
    parser.add_argument('operation', choices=['check', 'backup', 'maintenance', 'status', 'restic'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    restore_target = None
    if args.operation == 'restic' and args.arguments[:1] == ['restore']:
        for index, value in enumerate(args.arguments):
            if value == '--target' and index + 1 < len(args.arguments):
                restore_target = Path(args.arguments[index + 1]).expanduser().resolve()
                args.arguments[index + 1] = str(restore_target)
            elif value.startswith('--target='):
                restore_target = Path(value.split('=', 1)[1]).expanduser().resolve()
                args.arguments[index] = '--target=' + str(restore_target)
        if restore_target is None:
            parser.error('restore requires --target with an empty private scratch directory')
        restore_target.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = restore_target.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o7777 != 0o700:
            raise RuntimeError('Restore target must be owned by the current user with mode 0700')
        if any(restore_target.iterdir()):
            raise RuntimeError('Restore target must be empty; preserve existing files before retrying')
    raw = subprocess.check_output(['bash', '-ec', 'set -a; . "$1"; env -0', 'bash', args.settings])
    env = dict(item.decode().split('=', 1) for item in raw.split(b'\0') if b'=' in item)
    state = Path(env.get('BACKUP_STATE_DIR', '/var/lib/infra-backup'))
    if args.operation == 'check' and os.geteuid() == 0:
        subprocess.run(['python3', str(ROOT / 'infra-backup-declared.py'), 'check', '--live'], env=env, check=True)
        return
    if args.operation == 'status':
        for name in ('last-success', 'last-maintenance-success', 'last-result'):
            p = state / name
            print(name + ': ' + (p.read_text().strip() if p.exists() else 'not recorded'))
        return
    if args.operation == 'backup' and env.get('K3S_BACKUP_DIR'):
        # K3s is reached through the host's kubeconfig, so capture it here,
        # before the snapshot that includes K3S_BACKUP_DIR.
        try:
            subprocess.run([str(ROOT / 'infra-backup-k3s')], env=env, check=True)
        except BaseException:
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
            (state / 'last-result').write_text(json.dumps({'result': 'failed', 'operation': 'k3s-capture'}))
            raise
    target = [str(ROOT / ('infra-backup-maintenance' if args.operation == 'maintenance' else 'infra-backup'))]
    if args.operation == 'check':
        target = ['python3', str(ROOT / 'infra-backup-declared.py'), 'check', '--live']
    if args.operation == 'restic':
        target = ['restic', *args.arguments]
    if os.geteuid() == 0:
        subprocess.run(target, env=env, check=True)
        return
    config = json.loads(Path(env['BACKUP_TOOL_CONFIG']).read_text())
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache = Path(env['RESTIC_CACHE_DIR'])
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Plan on the host, where Compose and PyYAML are already installed. Mount
    # the same paths in the helper so manifests retain host recovery paths.
    paths = [Path(env['INFRA_DIR']), Path(args.settings).parent,
             Path(env['RCLONE_CONFIG']).parent, Path(env['RESTIC_PASSWORD_FILE']).parent]
    if args.operation in ('backup', 'check'):
        with tempfile.TemporaryDirectory(prefix='mount-plan-', dir=state) as tmp:
            subprocess.run(['python3', str(ROOT / 'infra-backup-declared.py'), 'plan', tmp], env=env, check=True)
            plan = json.loads((Path(tmp) / 'plan.json').read_text())
            paths += [Path(p) for p in plan['sources'] + plan['offline']]
            paths += [Path(p) for q in plan['quiesce'].values() for p in q.get('copy', [])]
    name = 'infra-backup-tool-' + uuid.uuid4().hex
    command = ['docker', 'run', '--rm', '--init', '--name', name, '--read-only',
               '--memory', '768m', '--cpus', '1.5', '--tmpfs', '/tmp:rw,size=128m']
    mounts = {p: (str(p), True) for p in paths}
    mounts.update({state: (str(state), False), cache: (str(cache), False),
                   Path('/var/run/docker.sock'): ('/var/run/docker.sock', False),
                   Path(yaml.__file__).parent: ('/backup-libs/yaml', True)})
    if restore_target is not None:
        mounts[restore_target] = (str(restore_target), False)
    for key, destination in [('docker_binary', '/usr/local/bin/docker'),
                             ('compose_binary', '/root/.docker/cli-plugins/docker-compose'),
                             ('restic_binary', '/usr/local/bin/restic'),
                             ('rclone_binary', '/usr/local/bin/rclone')]:
        mounts[Path(config[key])] = (destination, True)
    for source, (destination, readonly) in mounts.items():
        command += ['--mount', 'type=bind,src=' + str(source) + ',dst=' + destination + (',readonly' if readonly else '')]
    helper_env = {k: v for k, v in env.items() if k.startswith(('INFRA_', 'BACKUP_', 'RESTIC_', 'RCLONE_'))
                  or k in ('PATH', 'HOME', 'LANG', 'TZ', 'GOMEMLIMIT')}
    helper_env.update({'PYTHONPATH': '/backup-libs', 'BACKUP_CONTAINER': '1',
                  'BACKUP_OWNER_UID': str(os.getuid()), 'BACKUP_OWNER_GID': str(os.getgid())})
    # Docker inherits these without putting secret values in process arguments.
    for key in helper_env:
        command += ['--env', 'HOME=/root' if key == 'HOME' else key]
    command += ['--entrypoint', '/bin/bash', config['tool_image'], '-ec', 'exec "$@"', 'bash', *target]

    def terminate(signum, frame):
        subprocess.run(['docker', 'stop', '--time', '240', name], stdout=subprocess.DEVNULL, check=False)
        raise InterruptedError('Backup interrupted; inspect retained staging')

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, terminate)
    try:
        subprocess.run(command, env={**env, **helper_env}, check=True)
        if args.operation in ('backup', 'maintenance'):
            (state / 'last-result').write_text(json.dumps({'result': 'success', 'operation': args.operation,
                'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    except BaseException:
        if args.operation in ('backup', 'maintenance'):
            (state / 'last-result').write_text(json.dumps({'result': 'failed', 'operation': args.operation}))
        raise
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == '__main__':
    os.umask(0o077)
    main()
