#!/usr/bin/env python3
"""Validate media boundaries in an all-profile rendered host Compose document."""
import json
import sys

SERVICES = {'gluetun', 'qbittorrent', 'prowlarr', 'flaresolverr', 'sonarr',
            'radarr', 'jellyfin', 'makemkv', 'handbrake'}
MOUNTS = {
    'gluetun': {'/gluetun': False},
    'qbittorrent': {'/config': False, '/data/torrent': False},
    'prowlarr': {'/config': False},
    'flaresolverr': {'/config': False},
    'sonarr': {'/config': False, '/data/torrent': True, '/data/import/makemkv': True,
               '/data/import/handbrake': True, '/data/media/tv': False},
    'radarr': {'/config': False, '/data/torrent': True, '/data/import/makemkv': True,
               '/data/import/handbrake': True, '/data/media/movies': False},
    'jellyfin': {'/config': False, '/cache': False, '/data/media': True},
    'makemkv': {'/config': False, '/storage': False, '/output': False,
                '/opt/media-extract/makemkv-device-adapter.sh': True},
    'handbrake': {'/config': False, '/storage': True, '/output': False,
                  '/watch': False, '/trash': False},
}
PORTS = {'gluetun': [(8080, 8080), (18000, 8000)],
         'flaresolverr': [(18191, 8191)],
         'sonarr': [(8989, 8989)], 'radarr': [(7878, 7878)],
         'prowlarr': [(9696, 9696)], 'jellyfin': [(8096, 8096)],
         'makemkv': [(5800, 5800)], 'handbrake': [(5801, 5800)]}


def check(doc):
    services = doc['services']
    if not SERVICES.intersection(services):
        return []  # Other hosts need not run media.
    errors = []
    def need(ok, message):
        if not ok:
            errors.append(message)
    need(SERVICES.issubset(services), 'all nine media services must be present (render all profiles)')
    if not SERVICES.issubset(services):
        return errors
    mounts = {}
    for name in sorted(SERVICES):
        s = services[name]
        need(s.get('container_name') == 'media-' + name, name + ': stable container name')
        need(not s.get('privileged'), name + ': privileged forbidden')
        need(not s.get('pid') and not s.get('ipc') and not s.get('volumes_from'), name + ': no shared host namespaces or inherited mounts')
        need(not s.get('cap_add') or name == 'gluetun', name + ': additional capabilities forbidden')
        need(not s.get('devices') or name in {'gluetun', 'makemkv'}, name + ': devices forbidden')
        if name == 'qbittorrent':
            need(s.get('network_mode') == 'service:gluetun' and not s.get('networks'), 'qBittorrent must only share Gluetun network namespace')
            need(s.get('depends_on', {}).get('gluetun', {}).get('condition') == 'service_healthy', 'qBittorrent must wait for healthy VPN')
        else:
            need(not s.get('network_mode') and set(s.get('networks', [])) == {'media'}, name + ': media network only')
        ports = s.get('ports', [])
        if name not in PORTS:
            need(not ports, name + ': no published port')
        else:
            expected = {('0.0.0.0' if name == 'jellyfin' else '127.0.0.1', str(published), target, 'tcp')
                        for published, target in PORTS[name]}
            actual = {(p.get('host_ip'), str(p.get('published')), p.get('target'), p.get('protocol', 'tcp'))
                      for p in ports}
            need(len(ports) == len(expected) and actual == expected, name + ': preserve listener boundary')
        volumes = s.get('volumes', [])
        mounts[name] = {v['target']: v for v in volumes}
        need(len(volumes) == len(mounts[name]) and set(mounts[name]) == set(MOUNTS[name]), name + ': exact scoped mount targets')
        for target, ro in MOUNTS[name].items():
            v = mounts[name].get(target, {})
            need(v.get('type') == 'bind' and bool(v.get('read_only')) == ro
                 and v.get('bind', {}).get('create_host_path') is False, name + ': fail-closed bind/access at ' + target)
    def source(service, target):
        return mounts[service].get(target, {}).get('source')
    for name in ['sonarr', 'radarr']:
        need(source(name, '/data/torrent') == source('qbittorrent', '/data/torrent'), name + ': shared torrent source path')
        need(source(name, '/data/import/makemkv') == source('makemkv', '/output'), name + ': shared raw rip source')
        need(source(name, '/data/import/handbrake') == source('handbrake', '/output'), name + ': shared encode source')
    need(source('handbrake', '/storage') == source('makemkv', '/output'), 'HandBrake must read MakeMKV output')
    for name, leaf in [('sonarr', 'tv'), ('radarr', 'movies')]:
        need(source(name, '/data/media/' + leaf) == str(source('jellyfin', '/data/media')) + '/' + leaf, name + ': Jellyfin must see imported library')
    g = services['gluetun']
    need(g.get('cap_add') == ['NET_ADMIN'], 'Gluetun requires only NET_ADMIN')
    need(g.get('devices') == [{'source': '/dev/net/tun', 'target': '/dev/net/tun', 'permissions': 'rwm'}], 'Gluetun requires TUN device only')
    need(g.get('healthcheck', {}).get('test') == ['CMD', '/gluetun-entrypoint', 'healthcheck'], 'Gluetun health check must remain')
    need(str(g.get('environment', {}).get('FIREWALL_INPUT_PORTS')) == '8000,8080', 'VPN firewall admits only control API and download WebUI')
    m = services['makemkv']
    need(m.get('entrypoint') == ['/bin/sh', '/opt/media-extract/makemkv-device-adapter.sh'] and m.get('command') == ['/init'], 'MakeMKV adapter must hand off to /init')
    devices = m.get('devices', [])
    need(len(devices) == 2 and {d.get('target') for d in devices} == {'/dev/media-extract-sr', '/dev/media-extract-sg'}
         and all(d.get('permissions') == 'rwm' and d.get('source') for d in devices), 'MakeMKV requires exactly staged optical pair')
    for name in ['makemkv', 'handbrake']:
        need(services[name].get('profiles') == ['rip'], name + ': opt-in rip profile')
    return errors


if __name__ == '__main__':
    errors = check(json.load(sys.stdin))
    for error in errors:
        print('Media boundary: ' + error, file=sys.stderr)
    if errors:
        sys.exit(1)
    print('Media VPN, listener, mount and optical boundaries OK.')
