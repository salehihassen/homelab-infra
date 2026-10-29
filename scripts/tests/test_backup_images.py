"""Image recovery must follow running containers, even when tags have moved."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'extra_images', REPO / 'hosts/server-1/backup/infra-backup-extra-state.py')
extra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extra)


def container(service, image_id='sha256:old', image='helper:old'):
    return {'Name': '/' + service, 'Image': image_id,
            'Config': {'Image': image, 'Labels': {
                'com.docker.compose.project': 'opt',
                'com.docker.compose.service': service}}}


class BackupImageTests(unittest.TestCase):
    def test_older_tag_of_a_local_service_is_still_archived(self):
        plan = {'local_image_names': ['helper:new'],
                'local_image_services': ['helper', 'relay']}
        # Containerd's generated digest does not make a local image pullable.
        with patch.object(extra, 'command', return_value=json.dumps([
                {'Id': 'sha256:old', 'RepoDigests': ['helper@sha256:old']}])):
            images = extra.collect_local_images([container('relay')], plan)
        self.assertEqual(images, [{'container': 'relay', 'service': 'relay',
                                   'image': 'helper:old', 'image_id': 'sha256:old'}])

    def test_missing_image_fails_with_the_affected_container(self):
        missing = subprocess.CalledProcessError(1, ['docker', 'image', 'inspect'])
        with patch.object(extra, 'command', side_effect=missing):
            with self.assertRaisesRegex(RuntimeError, 'relay.*missing.*refusing'):
                extra.collect_local_images([container('relay')], {
                    'local_image_names': ['helper:old'], 'local_image_services': ['relay']})

    def test_save_uses_exact_ids_and_restore_override_preserves_each_service(self):
        # Both containers requested the same tag at different times.
        images = [dict(container='helper', service='helper', image='helper:latest',
                       image_id='sha256:old'),
                  dict(container='relay', service='relay', image='helper:latest',
                       image_id='sha256:new')]
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)

            def save(args, **kwargs):
                self.assertEqual(args[-2:], ['sha256:new', 'sha256:old'])
                self.assertNotIn('helper:latest', args)
                Path(args[args.index('--output') + 1]).write_bytes(b'archive')

            with patch.object(extra.subprocess, 'run', side_effect=save) as run:
                manifest = extra.save_local_images(stage, images)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(manifest['images'], images)
            self.assertEqual(manifest['bytes'], 7)
            override = json.loads((stage / 'images/compose.restore.json').read_text())
            self.assertEqual(override['services'], {
                'helper': {'image': 'sha256:old', 'pull_policy': 'never'},
                'relay': {'image': 'sha256:new', 'pull_policy': 'never'}})

    def test_shared_image_is_saved_once(self):
        images = [dict(container=s, service=s, image='helper:1', image_id='sha256:one')
                  for s in ['helper', 'relay']]
        with tempfile.TemporaryDirectory() as directory:
            def save(args, **kwargs):
                self.assertEqual(args.count('sha256:one'), 1)
                Path(args[args.index('--output') + 1]).write_bytes(b'archive')
            with patch.object(extra.subprocess, 'run', side_effect=save):
                extra.save_local_images(Path(directory), images)

    def test_conflicting_replicas_fail_before_saving(self):
        images = [dict(container='replica-' + str(i), service='helper',
                       image='helper:latest', image_id='sha256:' + str(i)) for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(extra.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'helper.*multiple running images'):
                    extra.save_local_images(Path(directory), images)
            run.assert_not_called()
