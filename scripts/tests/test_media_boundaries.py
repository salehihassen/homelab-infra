"""Mutations must not weaken media's least-privilege contracts."""
import copy
import importlib.util
from pathlib import Path
import unittest

import yaml

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('media_check', REPO / 'scripts/check-media.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class MediaBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load((REPO / 'hosts/server-1/media-extract/compose.yaml').read_text())

    def test_baseline(self):
        self.assertEqual(media.check(self.doc), [])

    def test_other_hosts_without_media(self):
        self.assertEqual(media.check({'services': {}}), [])

    def test_writable_source_or_library_is_rejected(self):
        for service, target in [('sonarr', '/data/torrent'), ('radarr', '/data/import/makemkv'),
                                ('jellyfin', '/data/media'), ('handbrake', '/storage')]:
            with self.subTest(service=service):
                doc = copy.deepcopy(self.doc)
                next(v for v in doc['services'][service]['volumes'] if v['target'] == target)['read_only'] = False
                self.assertTrue(media.check(doc))

    def test_missing_bind_source_must_not_create_empty_state(self):
        for service in media.SERVICES:
            with self.subTest(service=service):
                doc = copy.deepcopy(self.doc)
                doc['services'][service]['volumes'][0]['bind']['create_host_path'] = True
                self.assertTrue(media.check(doc))

    def test_vpn_bypass_is_rejected(self):
        for change in [{'network_mode': 'host'}, {'networks': ['media']}, {'depends_on': {}}]:
            doc = copy.deepcopy(self.doc)
            doc['services']['qbittorrent'].update(change)
            self.assertTrue(media.check(doc))

    def test_public_admin_or_new_port_is_rejected(self):
        for service in media.PORTS:
            if service == 'jellyfin':
                continue
            for port in range(len(media.PORTS[service])):
                with self.subTest(service=service, port=port):
                    doc = copy.deepcopy(self.doc)
                    doc['services'][service]['ports'][port]['host_ip'] = '0.0.0.0'
                    self.assertTrue(media.check(doc))
        self.doc['services']['flaresolverr']['ports'].append(
            {'host_ip': '127.0.0.1', 'target': 8192, 'published': '18192'})
        self.assertTrue(media.check(self.doc))

    def test_shared_source_and_library_identity(self):
        for target in ['/data/torrent', '/data/media/tv']:
            doc = copy.deepcopy(self.doc)
            next(v for v in doc['services']['sonarr']['volumes'] if v['target'] == target)['source'] = '/wrong'
            self.assertTrue(media.check(doc))

    def test_broad_mount_or_privilege_is_rejected(self):
        self.doc['services']['sonarr']['volumes'].append({'type': 'bind', 'source': '/', 'target': '/host'})
        self.assertTrue(media.check(self.doc))
        self.setUp()
        self.doc['services']['sonarr']['privileged'] = True
        self.assertTrue(media.check(self.doc))

    def test_optical_handoff_and_profile_are_required(self):
        for change in [{'command': []}, {'devices': []}, {'profiles': []}]:
            doc = copy.deepcopy(self.doc)
            doc['services']['makemkv'].update(change)
            self.assertTrue(media.check(doc))
