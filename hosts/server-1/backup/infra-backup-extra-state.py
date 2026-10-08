#!/usr/bin/env python3
"""Native dumps of other running PostgreSQL instances; cold archives of unused state."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

def command(args):
    return subprocess.check_output(args, text=True)

def containers():
    ids = command(['docker', 'ps', '-q']).split()
    return json.loads(command(['docker', 'inspect', *ids])) if ids else []

def overlaps(a, b):
    a, b = str(Path(a).resolve()), str(Path(b).resolve())
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')

def in_use(path, current, views=()):
    # Declared broad read-only views are observers, not writers of offline state.
    # Keep treating every other overlapping mount as in use.
    return [x['Name'].lstrip('/') for x in current
            if any(overlaps(path, mount['Source'])
                   and not (not mount.get('RW', True) and mount['Source'] in views)
                   for mount in x['Mounts'])]

def needs_image_archive(image_name, image_info, built_images):
    return image_name in built_images or not image_info.get('RepoDigests')


def main(stage):
    stage = Path(stage)
    assert os.geteuid() == 0
    assert stage.parent == Path(os.environ.get('BACKUP_STATE_DIR', '/var/lib/infra-backup')) and stage.name == 'staging'
    plan = json.loads((stage / 'plan.json').read_text())
    views = plan.get('views', [])
    running = containers()
    native = []
    for container in running:
        name = container['Name'].lstrip('/')
        if name == 'postgres' or (container['Config'].get('Labels') or {}).get('infra.recovery-drill') == 'true' or not container['Config']['Image'].startswith(('postgres:', 'pgvector/pgvector:', 'ghcr.io/ferretdb/postgres-documentdb:')):
            continue
        assert re.fullmatch(r'[A-Za-z0-9_.-]+', name)
        destination = stage / 'database-containers' / (name + '.sql')
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        with destination.open('wb') as output:
            subprocess.run(['docker', 'exec', container['Id'], 'sh', '-ec',
                            'exec pg_dumpall -U "${POSTGRES_USER:-postgres}"'],
                           stdout=output, check=True, timeout=600)
        with destination.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        native.append({'container': name, 'image_id': container['Image'],
                       'image': container['Config']['Image'], 'backup': str(destination),
                       'sha256': digest, 'bytes': destination.stat().st_size,
                       'mounts': [{'type': m['Type'], 'source': m['Source'], 'destination': m['Destination']}
                                  for m in container['Mounts']]})


    archive_sources = []
    volumes = []
    names = command(['docker', 'volume', 'ls', '-q']).split()
    for volume in json.loads(command(['docker', 'volume', 'inspect', *names])) if names else []:
        if volume['Driver'] != 'local':
            raise RuntimeError('Non-local volume needs an explicit recovery policy: ' + volume['Name'])
        name, path = volume['Name'], Path(volume['Mountpoint'])
        assert re.fullmatch(r'[A-Za-z0-9_.-]+', name)
        attached = in_use(path, running, views)
        if not attached:
            archive_sources.append(path)
        volumes.append({'name': name, 'driver': volume['Driver'], 'source': str(path),
                        'running_containers': attached,
                        'method': 'cold_archive' if not attached else
                            ('native_postgresql' if any(overlaps(path, m['source']) for x in native for m in x['mounts'])
                             else 'active_not_archived')})

    # `offline` entries from the backup declarations.
    legacy = plan['offline']
    for name in legacy:
        path = Path(name)
        if not path.exists():
            continue
        used = in_use(path, running, views)
        if used:
            raise RuntimeError(f'Legacy archive source is running: {name}: {used}')
        archive_sources.append(path)

    destination = stage / 'archived-state' / 'offline-state.tar'
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    if archive_sources:
        # A running database is never copied here. Refuse overlaps before AND after capture.
        for path in archive_sources:
            if in_use(path, containers(), views):
                raise RuntimeError('Archive source became active before capture: ' + str(path))
        subprocess.run(['tar', '--create', '--file', str(destination), '--directory', '/',
                        '--format=pax', '--sort=name', '--acls', '--xattrs', '--numeric-owner',
                        '--pax-option=delete=atime,delete=ctime', '--',
                        *(str(path).lstrip('/') for path in archive_sources)], check=True, timeout=900)
        for path in archive_sources:
            if in_use(path, containers(), views):
                raise RuntimeError('Archive source became active during capture: ' + str(path))
        with destination.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        archive = {'backup': str(destination), 'sha256': digest,
                   'bytes': destination.stat().st_size, 'sources': [str(p) for p in archive_sources],
                   'metadata': 'PAX archive; numeric owners, ACLs and extended attributes'}
    else:
        archive = None

    # Refresh Komodo's application export, rather than snapshotting yesterday's files.
    komodo_backup = None
    if '/opt/komodo/backups' in plan['sources']:
        core = next((x for x in running if x['Name'] == '/komodo-core'), None)
        if not core:
            raise RuntimeError('Komodo Core is unavailable; refusing a stale database export')
        result = subprocess.run(['docker', 'exec', core['Id'], 'km', 'database', 'backup', '--yes'],
                                capture_output=True, timeout=300)
        if result.returncode:
            raise RuntimeError('Komodo native backup failed; refusing an incomplete snapshot')
        folders = sorted(Path('/opt/komodo/backups').glob('????-??-??_??-??-??'))
        if not folders:
            raise RuntimeError('Komodo native backup produced no export directory')
        folder = folders[-1]
        files = sorted(folder.glob('*.gz'))
        if not files:
            raise RuntimeError('Komodo native backup produced no collections')
        komodo_backup = {'path': str(folder), 'collections': len(files),
                         'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}

    # Preserve locally built running stack images; restoring must not depend on
    # future package indexes or on a source tree rebuilding identical binaries.
    local_images = []
    for container in running:
        if (container['Config'].get('Labels') or {}).get('com.docker.compose.project') != 'opt':
            continue
        info = json.loads(command(['docker', 'image', 'inspect', container['Image']]))[0]
        if needs_image_archive(container['Config']['Image'], info, plan['local_image_names']):
            local_images.append({'container': container['Name'].lstrip('/'),
                                 'image': container['Config']['Image'], 'image_id': container['Image']})
    image_archive = None
    local_archive = None
    if local_images:
        destination = stage / 'images' / 'local-images.tar'
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(['docker', 'image', 'save', '--output', str(destination),
                        *sorted({x['image'] for x in local_images})], check=True, timeout=1800)
        with destination.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        local_archive = {'backup': str(destination), 'sha256': digest,
                         'bytes': destination.stat().st_size, 'images': local_images}
        bridge = next((x for x in local_images if x['container'] == 'protonmail-bridge'), None)
        if bridge:
            image_archive = {'image_id': bridge['image_id'], 'backup': str(destination),
                             'sha256': digest, 'bytes': destination.stat().st_size}

    manifest = {'extra_postgresql': native, 'volumes': volumes,
                'offline_archive': archive, 'protonmail_image': image_archive,
                'local_images': local_archive, 'komodo_backup': komodo_backup}
    (stage / 'inventory/extra-state-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Created {len(native)} extra PostgreSQL dumps and archived {len(archive_sources)} offline state roots')


if __name__ == "__main__":
    main(sys.argv[1])
