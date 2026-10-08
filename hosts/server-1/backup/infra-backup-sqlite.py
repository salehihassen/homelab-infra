#!/usr/bin/env python3
"""Create bounded online SQLite backups and a manifest; never write source DBs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from urllib.parse import quote

stage = Path(sys.argv[1])
# Declared in hosts/<host>/*/backup.yaml and host.yaml; see infra-backup-declared.py.
plan = json.loads((stage / 'plan.json').read_text())
sources = {Path(p): None for p in plan['sqlite']}
roots = [Path(p) for p in plan['sqlite_scan']]
for root in roots:
    if root.exists():
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules', 'Cache', '.cache', '__pycache__'}]
            for name in files:
                if name.endswith(('.db', '.sqlite', '.sqlite3')):
                    p = Path(base, name)
                    if p.is_symlink():
                        raise RuntimeError(f'Refusing SQLite symlink: {p}')
                    with p.open('rb') as f:
                        if f.read(16) == b'SQLite format 3\x00':
                            sources.setdefault(p, None)

manifest = []
excludes = []
for source in sorted(sources):
    if not source.is_file():
        # A declared database that vanished is a failure, not a quiet skip.
        raise RuntimeError(f'Declared SQLite database is missing: {source}')
    destination = stage / 'database-files' / str(source).lstrip('/')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f'Online SQLite export: {source}', flush=True)
    deadline = time.monotonic() + 120
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError(f'SQLite online backup exceeded 120 seconds: {source}')
    with sqlite3.connect('file:' + quote(str(source)) + '?mode=ro', uri=True, timeout=10) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            check = dst.execute('PRAGMA quick_check').fetchall()
            if check != [('ok',)]:
                raise RuntimeError(f'SQLite integrity check failed: {source}: {check}')
    # Literal filenames: do not accidentally exclude unrelated files with a prefix.
    excludes.extend(str(source) + suffix for suffix in ['', '-wal', '-shm', '-journal'])
    with destination.open('rb') as exported:
        digest = hashlib.file_digest(exported, 'sha256').hexdigest()
    # The live database is excluded, so its ownership must travel with the
    # consistent export. It may differ from the containing directory (pgAdmin).
    info = source.stat()
    shutil.copystat(source, destination)
    os.chown(destination, info.st_uid, info.st_gid)
    manifest.append({'source': str(source), 'backup': str(destination), 'status': 'ok',
                     'size': destination.stat().st_size, 'sha256': digest, 'quick_check': 'ok',
                     'uid': info.st_uid, 'gid': info.st_gid, 'mode': info.st_mode & 0o7777})
(stage / 'inventory/sqlite-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
(stage / 'sqlite-excludes.txt').write_text('\n'.join(excludes) + '\n')
print(f'Created {sum(x["status"] == "ok" for x in manifest)} consistent SQLite exports')
