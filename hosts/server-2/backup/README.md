# Host backup settings

Use the [shared backup playbook](../../../docs/backups.md) and tooling in
`backup/` for both hosts. Server-2 uploads one nightly encrypted host snapshot
directly to its own B2 bucket using a dedicated restricted application key.

Private settings live in `/opt/infra-backup/backup.env`. Create
`/opt/infra-backup/tool.json` from `tool.example.json` with mode `0600`; it holds
helper binary paths and the pinned tool image. `BACKUP_TOOL_CONFIG` points to it.
Use the common
`backup/run.py --settings /opt/infra-backup/backup.env` interface for `check`,
`backup`, `maintenance`, `status` and `restic` operations. No credentials are
tracked. Existing NAS and gateway snapshots remain recovery sources.

The `K3S_*` settings add the public K3s cluster's database dumps and inputs to
each snapshot; see "The public K3s cluster" in the backup playbook.
