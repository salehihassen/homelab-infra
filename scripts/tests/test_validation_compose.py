"""Validation tooling must not require changing the host's Compose install."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class ValidationComposeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Hooks can export GIT_INDEX_FILE/GIT_DIR for the real checkout. Never
        # let a disposable test repository read or change that index.
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.environment = {**inherited, "PATH": f"{self.bin}:{os.environ['PATH']}",
                            "XDG_CACHE_HOME": str(self.root / "cache"),
                            "CAPTURE": str(self.root / "capture")}

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def copy_script(self, name):
        path = self.root / "scripts" / name
        shutil.copyfile(REPO / "scripts" / name, path)
        return path

    def invoke(self, script, *args):
        return subprocess.run(["bash", str(script), *args], cwd=self.root,
                              env=self.environment, text=True, capture_output=True, timeout=10)

    def test_matching_native_compose_needs_no_download_and_preserves_arguments(self):
        script = self.copy_script("with-validation-compose.sh")
        self.executable("docker", '#!/bin/sh\nprintf "v5.5.1\\n"\n')
        self.executable("curl", '#!/bin/sh\nexit 99\n')
        result = self.invoke(script, "printf", "%s\\n", "value with spaces", "second")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "value with spaces\nsecond\n")
        self.assertFalse((self.root / "cache").exists())

    def test_newer_native_compose_dispatches_validation_instead_of_rejecting_it(self):
        script = self.copy_script("validate-stack.sh")
        self.executable("docker", '#!/bin/sh\nprintf "5.6.1\\n"\n')
        helper = self.root / "scripts/with-validation-compose.sh"
        helper.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\nexit 42\n')
        result = self.invoke(script, "compose")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((self.root / "capture").read_text().splitlines(),
                         ["bash", str(script), "compose"])
        self.assertNotIn("is required to match CI", result.stderr)

    def test_unverified_download_is_never_executed_or_used_as_fallback(self):
        script = self.copy_script("with-validation-compose.sh")
        self.executable("docker", '#!/bin/sh\nprintf "5.6.1\\n"\n')
        self.executable("uname", '#!/bin/sh\nprintf "Linux x86_64\\n"\n')
        self.executable("curl", '''#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then
    shift
    printf '#!/bin/sh\\ntouch "$CAPTURE"\\n' > "$1"
    exit 0
  fi
  shift
done
exit 1
''')
        result = self.invoke(script, "touch", str(self.root / "command-ran"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr.lower())
        self.assertFalse((self.root / "capture").exists())
        self.assertFalse((self.root / "command-ran").exists())
        self.assertFalse(any((self.root / "cache").rglob("download.*")))

    def test_snapshot_still_validates_index_instead_of_unstaged_content(self):
        script = self.copy_script("validate-snapshot.sh")
        helper = self.root / "scripts/with-validation-compose.sh"
        helper.write_text('#!/bin/sh\nprintf wrapped > "$CAPTURE"\nexec "$@"\n')
        validator = self.root / "scripts/validate-stack.sh"
        validator.write_text('#!/bin/sh\ncat "$(dirname "$0")/../fixture"\n')
        fixture = self.root / "fixture"
        fixture.write_text("staged\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, env=self.environment, check=True)
        subprocess.run(["git", "add", "scripts/validate-stack.sh", "fixture"],
                       cwd=self.root, env=self.environment, check=True)
        fixture.write_text("unstaged\n")
        result = self.invoke(script, "--index", "compose")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "staged\n")
        self.assertEqual((self.root / "capture").read_text(), "wrapped")
        self.assertEqual(fixture.read_text(), "unstaged\n")


if __name__ == "__main__":
    unittest.main()
