#!/usr/bin/env python3
"""Exercise real Codex sandbox permissions without model calls or AI fees.

Use --image for a disposable, networkless container with empty tmpfs data roots;
otherwise test the running application container. Fixtures are always removed.
"""

import argparse
from pathlib import Path
import subprocess


HARNESS = r'''
import ctypes
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

os.makedirs('/home/node/.codex', exist_ok=True)
workspace = Path(tempfile.mkdtemp(prefix='.sandbox-check-', dir='/workspace'))
outside = Path(tempfile.mkdtemp(prefix='.sandbox-check-', dir='/home/node'))
fixture = workspace / 'input.txt'
fixture.write_text('sandbox-fixture')

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'sandbox-network-fixture')

    def log_message(self, *args):
        pass

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = 'http://127.0.0.1:' + str(server.server_port)

def run(label, mode, program, network=False):
    command = ['codex', '-c', 'sandbox_mode=' + json.dumps(mode)]
    if network:
        command += ['-c', 'sandbox_workspace_write.network_access=true']
    command += ['sandbox', '--', 'python3', '-c', program]
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(label + ': ' + (result.stderr or result.stdout)[-2000:])
    print('PASS ' + label, flush=True)

try:
    run('read-only can read the fixture', 'read-only',
        'from pathlib import Path; assert Path("input.txt").read_text() == "sandbox-fixture"')
    denied = """
from pathlib import Path
try:
    Path(TARGET).write_text('unexpected-write')
except PermissionError:
    pass
except OSError as error:
    assert error.errno == 30, error
else:
    raise AssertionError('write was allowed')
"""
    run('read-only denies workspace writes', 'read-only',
        'TARGET="denied.txt"\n' + denied)
    run('workspace-write permits workspace writes', 'workspace-write',
        'from pathlib import Path; Path("allowed.txt").write_text("allowed")')
    assert (workspace / 'allowed.txt').read_text() == 'allowed'
    run('workspace-write denies writes to the separate home mount', 'workspace-write',
        'TARGET=' + repr(str(outside / 'denied.txt')) + '\n' + denied, network=True)
    fetch = 'import urllib.request; assert urllib.request.urlopen(' + repr(url) + ', timeout=3).read() == b"sandbox-network-fixture"'
    run('network-enabled sandbox can fetch a supplied URL', 'workspace-write', fetch, network=True)
    blocked = """
import urllib.request
try:
    urllib.request.urlopen(URL, timeout=2)
except OSError:
    pass
else:
    raise AssertionError('network request was allowed')
"""
    run('read-only denies direct network access', 'read-only', 'URL=' + repr(url) + '\n' + blocked)
    run('workspace-write without network permission denies network access', 'workspace-write',
        'URL=' + repr(url) + '\n' + blocked)
    # The outer container must still lack mount privileges and all capabilities.
    status = Path('/proc/self/status').read_text()
    assert 'CapEff:\t0000000000000000' in status
    assert 'NoNewPrivs:\t1' in status
    target = workspace / 'mount-point'
    target.mkdir()
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.mount(b'none', os.fsencode(target), b'tmpfs', 0, None) == -1
    assert ctypes.get_errno() == 1
    print('PASS outer container has zero capabilities and cannot mount filesystems')
finally:
    server.shutdown()
    server.server_close()
    shutil.rmtree(workspace)
    shutil.rmtree(outside)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='opt-career-ops-1')
    parser.add_argument('--image', help='test a disposable container instead of the live service')
    parser.add_argument('--seccomp', type=Path, default=Path(__file__).parent / 'security/seccomp.json')
    args = parser.parse_args()
    if args.image:
        command = [
            'docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
            '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges:true', '--security-opt', 'apparmor=career-ops-sandbox',
            '--security-opt', 'seccomp=' + str(args.seccomp.resolve()),
        ]
        for directory in ['/tmp', '/workspace', '/home/node']:
            command += ['--tmpfs', directory + ':rw,nosuid,size=128m,uid=1000,gid=1000']
        command += ['--entrypoint', 'python3', args.image, '-']
    else:
        command = ['docker', 'exec', '-i', '--workdir', '/workspace', args.container, 'python3', '-']
    subprocess.run(command, input=HARNESS, text=True, check=True)


if __name__ == '__main__':
    main()
