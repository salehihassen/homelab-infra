#!/usr/bin/env python3
"""Local destination maintenance; the application host has append-only access."""
import datetime
import json
import os
from pathlib import Path
import subprocess

os.umask(0o077)
root = Path(os.environ.get("RECEIVER_RUNTIME", str(Path.home() / ".local/share/server-2-backup-receiver")))
config = json.loads((root / "maintenance.json").read_text())
env = {**os.environ, "RESTIC_REPOSITORY": config["repository"], "RESTIC_PASSWORD_FILE": str(root / "secrets/restic-password")}
for args in [["forget", "--group-by", "host,tags", "--keep-daily", "14", "--keep-weekly", "8", "--keep-monthly", "12", "--prune"], ["check", "--read-data-subset=10%"]]:
    subprocess.run([str(root / "restic"), *args], env=env, check=True)
(root / "maintenance-status.json").write_text(json.dumps({"result": "success", "last_success": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
