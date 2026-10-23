"""Media maintenance helpers must retain their safety guardrails."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MEDIA = Path('hosts/server-1/media-extract')


class MediaToolingTests(unittest.TestCase):
    def test_shell_syntax_error_fails_compose_validation(self):
        # Stub unrelated checks; exercise the actual validator's exit status.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            shutil.copyfile(REPO / 'scripts/validate-stack.sh', root / 'scripts/validate-stack.sh')
            media_scripts = root / MEDIA / 'scripts'
            media_scripts.mkdir(parents=True)
            (media_scripts / 'broken.sh').write_text('#!/bin/sh\nif then\n')
            binaries = root / 'bin'
            binaries.mkdir()
            stubs = {
                'docker': '#!/bin/sh\nif [ "$2" = version ]; then echo 5.5.1; else echo "{}"; fi\n',
                'python3': '#!/bin/sh\ncat >/dev/null\n',
            }
            for name, content in stubs.items():
                executable = binaries / name
                executable.write_text(content)
                executable.chmod(0o755)
            result = subprocess.run(
                ['bash', str(root / 'scripts/validate-stack.sh'), 'compose'],
                env={**os.environ, 'INFRA_HOST': 'server-1',
                     'PATH': f'{binaries}:{os.environ["PATH"]}'},
                text=True, capture_output=True, input='', timeout=10,
            )
            self.assertIn('Syntax error', result.stderr)
            self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_runtime_state_is_ignored_but_source_is_not(self):
        # Snapshot validation has no .git; use a disposable repository.
        # Hook Git variables must not redirect init/check-ignore to the caller.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('GIT_')}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], cwd=root,
                           env=environment, check=True)
            shutil.copyfile(REPO / '.gitignore', root / '.gitignore')
            for name in ['.env', 'config/private', 'data/private', 'migration/evidence.json',
                         'evidence/report.json', 'backup/restic.env', 'backup/restic-password']:
                with self.subTest(ignored=name):
                    result = subprocess.run(
                        ['git', 'check-ignore', '-q', '--no-index', str(MEDIA / name)],
                        cwd=root, env=environment,
                    )
                    self.assertEqual(result.returncode, 0)
            for name in ['.env.example', 'vpn.env.example', 'backup.yaml', 'compose.yaml',
                         'scripts/ci-validate.sh', 'systemd/99-media-extract-optical.rules']:
                with self.subTest(source=name):
                    result = subprocess.run(
                        ['git', 'check-ignore', '-q', '--no-index', str(MEDIA / name)],
                        cwd=root, env=environment,
                    )
                    self.assertEqual(result.returncode, 1)

    def test_optical_helper_recommends_current_env_names(self):
        # Do not enumerate or probe the host's physical optical devices.
        helper = (REPO / MEDIA / 'scripts/find-optical-drive.sh').read_text()
        template = (REPO / MEDIA / '.env.example').read_text()
        for name in ['MEDIA_DVD_DEVICE', 'MEDIA_DVD_SG_DEVICE']:
            self.assertIn(f"printf '{name}=%s", helper)
            self.assertIn(f'{name}=', template)


if __name__ == '__main__':
    unittest.main()
