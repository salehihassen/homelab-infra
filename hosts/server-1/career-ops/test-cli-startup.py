#!/usr/bin/env python3
"""Check real Codex daemon startup without prompts, model calls or saved sessions.

An isolated temporary home keeps the deployment's configuration and daemon alone.
Use --image to check a disposable networkless container before deployment.
"""

import argparse
from pathlib import Path
import subprocess


HARNESS = r'''
import json
import os
from pathlib import Path
import subprocess
import tempfile

with tempfile.TemporaryDirectory(prefix='.codex-startup-', dir='/home/node') as scratch:
    env = dict(os.environ, HOME=scratch, CODEX_HOME=scratch + '/.codex')
    Path(env['CODEX_HOME']).mkdir(mode=0o700)
    command = ['codex', 'app-server', 'daemon']
    startup_ok = False
    try:
        started = subprocess.run(command + ['start'], env=env,
                                 # First start copies the managed CLI package to disk.
                                 capture_output=True, text=True, timeout=90)
        if started.returncode:
            raise RuntimeError('daemon startup failed: ' + started.stderr[-4000:])
        startup_ok = True
        print('PASS Codex daemon startup (including ps process identity)', flush=True)
        version = subprocess.run(command + ['version'], env=env,
                                 capture_output=True, text=True, timeout=15, check=True)
        info = json.loads(version.stdout)
        assert info['status'] == 'running', info
        assert info['backend'] == 'pid', info
        assert info['cliVersion'] == info['appServerVersion'] == info['managedCodexVersion'], info
        print('PASS CLI and running daemon versions match: ' + info['cliVersion'], flush=True)
    finally:
        stopped = subprocess.run(command + ['stop'], env=env,
                                 capture_output=True, text=True, timeout=15)
        if stopped.returncode and startup_ok:
            raise RuntimeError('daemon cleanup failed: ' + stopped.stderr[-2000:])
    print('PASS isolated daemon stopped; no model calls or deployment settings changed')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--container', default='opt-career-ops-1')
    target.add_argument('--image', help='check a disposable image instead of the live service')
    parser.add_argument('--seccomp', type=Path,
                        default=Path(__file__).parent / 'security/seccomp.json')
    args = parser.parse_args()
    if args.image:
        command = [
            'docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
            '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges:true', '--security-opt', 'apparmor=career-ops-sandbox',
            '--security-opt', 'seccomp=' + str(args.seccomp.resolve()),
        ]
        for directory in ['/tmp', '/workspace', '/home/node']:
            size = '1g' if directory == '/home/node' else '128m'
            executable = ',exec' if directory == '/home/node' else ''
            command += ['--tmpfs', directory + ':rw' + executable + ',nosuid,size=' + size + ',uid=1000,gid=1000']
        command += ['--workdir', '/workspace', '--entrypoint', 'python3', args.image, '-']
    else:
        command = ['docker', 'exec', '-i', '--workdir', '/workspace',
                   args.container, 'python3', '-']
    subprocess.run(command, input=HARNESS, text=True, check=True)


if __name__ == '__main__':
    main()
