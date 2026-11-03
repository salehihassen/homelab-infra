# Backups

Both hosts use the shared tooling in `backup/`: one encrypted, deduplicated
Restic snapshot per host each night, uploaded directly to its own Backblaze B2
repository through rclone. Each host has a separate bucket-restricted application
key and repository password. Never put either host's credentials in Git.

The shared timers run nightly at 03:15 plus up to 30 minutes jitter, and weekly
on Sunday at 05:15 plus up to one hour jitter. Maintenance retains seven daily,
five weekly and two monthly snapshots, checks metadata and reads a ten-percent
data sample. Each host's repository, host tag and recovery password remain distinct.

Server-1 runs system units as root. Server-2 runs user units; `backup/run.py`
uses a bounded Docker helper to read files with their numeric ownership and call
the same runner/exporters. It requires Docker access, not interactive sudo.
This helper is an administrative capability, not an unprivileged sandbox.

What gets backed up is declared next to the thing being backed up:

- `hosts/<host>/<stack>/backup.yaml` for each stack,
- `hosts/<host>/backup/host.yaml` for everything that belongs to no stack
  (host configuration, backup tooling, retired data),
- the untracked `hosts/<host>/backup/host.local.yaml` for paths that identify
  the owner or machine (home directories, source repositories, other apps);
  template: `host.local.yaml.example`.

Settings come from `/etc/infra-backup/backup.env` for system units, or
`/opt/infra-backup/backup.env` for user units. Templates live under each host.
User-mode helper binary/image paths are private `BACKUP_TOOL_CONFIG` inputs.

The shared runner (`backup/infra-backup`, installed to `/usr/local/sbin` on server-1)
reads these declarations from this repository at run time. It **fails** instead
of uploading a partial snapshot when:

- a bind mount, `env_file` or secret file used by any Compose service is
  neither backed up nor excluded with a reason,
- the shared PostgreSQL cluster contains a database no stack declares, or a
  declared one is missing,
- a declared path or SQLite database does not exist,
- an `offline` path is in use by a running container.

Native dumps of additional PostgreSQL containers can be disabled with the
`infra.backup=disabled` container label only when all persistent bind/volume
mounts are beneath explicit `exclude` paths. Unlabelled containers, partial
exclusions, restic glob patterns, and live files replaced by consistent exports
do not disable database dumps. This lets experimental stacks remain inactive
without changing retained stacks' coverage.
Image discovery accepts both tagged and digest-pinned PostgreSQL references.

## Declaration format

```yaml
volumes:          # explicit existing Docker local volumes
  - example_data
adapter: nextcloud-aio  # only for the AIO stack; omit for ordinary apps
save_images:      # private-registry services that must be recoverable offline
  - example
paths:            # copied as-is (absolute paths)
  - /opt/example
exclude:          # never uploaded; every entry needs a reason
  /opt/example/cache: rebuilt automatically
sqlite:           # copied with SQLite's online backup API, integrity-checked;
  - /opt/example/data/app.db   # the live file and its -wal/-shm are excluded
sqlite_scan:      # directories searched for SQLite files to copy the same way
  - /opt/example/db
postgres:         # databases in the shared cluster, dumped with pg_dump -Fc
  - example
quiesce:          # pause these containers while dumping this stack's
  method: stop   # optional; pause is the existing default
  services: [example, example-worker]   # databases and copying `copy`, so files
  copy:                                 # and database agree; always resumed
    - /opt/example/repositories
offline:          # retired state: tar-archived only while nothing uses it
  - /opt/old-service
views:            # read-only mounts of data that other declarations own
  - /opt          # (e.g. an agent that needs to see paths); must be :ro
exclude_patterns: # host.yaml only: restic glob patterns (caches, build output)
  - "**/node_modules"
```

Use `quiesce` when database and files must agree at one point in time. List
all writers. Copies of writable bind mounts and declared volumes are staged
before writers resume; uploads happen after resumption. SQLite exports use the
staged copy for coordinated groups and the online API for other databases.

The Nextcloud adapter discovers AIO children for capture only, refuses unknown
volumes/binds, enables maintenance, stops the writers/controller, exports PG
with globals/ownership/grants and saves Redis. PG/Redis remain running; live PG
storage is excluded. All originally running writers are restarted even on failure.
An existing maintenance state is preserved by aborting before changing it.
Failed staging is retained for inspection; never blindly delete it and retry.

## Snapshot layout

Live paths keep their host paths. Consistent copies live under the staging
directory, which is part of every snapshot:

```
/var/lib/infra-backup/staging/
  plan.json, excludes.txt            # what this run decided to back up
  databases/postgres/globals.sql     # roles and other cluster globals
  databases/postgres/<db>.dump       # pg_dump --format=custom per database
  database.dump, globals.sql         # Nextcloud adapter native PostgreSQL exports
  database-attributes.sql            # Nextcloud database ownership and grants
  postgres-counts.{sql,txt}           # Nextcloud recovery verification inputs
  database-files/<source path>       # SQLite online copies, mirroring the host
  quiesced/<source path>             # files copied while their writers were paused
  database-containers/<name>.sql     # other running Postgres containers (demos)
  archived-state/offline-state.tar   # `offline` paths
  images/local-images.tar           # running locally built stack images
  images/compose.restore.json        # service -> exact saved image ID; disables pulls
  inventory/                         # containers, mounts, packages, disks
```

## Checks you can run without root

```bash
python3 backup/infra-backup-declared.py --host <host> check --live
# On the selected host, using its installed settings:
python3 backup/run.py --settings <settings-path> check
```

`check` also runs in `install.sh` and at the start of every backup.

To take a backup now and confirm the service succeeded:

```bash
# Server-1: sudo; server-2: no sudo. Settings paths are above.
python3 backup/run.py --settings <settings-path> backup
python3 backup/run.py --settings <settings-path> status
# Scheduled jobs have the same names; add --user on user-service hosts.
systemctl [--user] show infra-backup.service -p Result -p ExecMainStatus
journalctl [--user] -u infra-backup.service -n 25 --no-pager
```

Expect `Result=success`, `ExecMainStatus=0`, a saved snapshot ID, and a
repository check without errors. This confirms capture and upload; recovery
needs the separate restore drill below.

Every run also takes a fresh Komodo application database export and a native
dump of its DocumentDB/PostgreSQL cluster. Broad read-only `views` are ignored
by the cold-archive writer check; other overlapping mounts still block it.
Local images are saved together so shared layers are stored once, with image
IDs and archive hashes in `inventory/extra-state-manifest.json`.
Compose build declarations identify local images even when Docker's
containerd image store reports registry-style digests for them.
This includes sidecars sharing a locally built image and running older tags.
Capture uses running image IDs, never the current targets of mutable tags.
A missing running image fails capture with the affected container's name;
recreate that service from its reviewed Compose image before retrying. Do not
substitute a different image under the old tag or skip its recovery artifact.
After loading the archive, use `images/compose.restore.json` as the last Compose
file and start with `--no-build --pull never`. It selects the saved IDs even if
tags have moved or two services used different images under the same tag.
Keep this override for recovery until deliberately deploying new images.
SQLite exports retain the live database's numeric owner and permissions;
those values are also recorded in `inventory/sqlite-manifest.json`. Restore
that metadata rather than guessing the owner from the containing directory.

Env files are not in Git, so the snapshot is their only copy off the host.
Keep the restic password and B2/rclone credentials outside the host as well: copies inside
an encrypted snapshot cannot bootstrap access to that snapshot.

## Changing the runner

Edit shared scripts in `backup/` or the host unit settings. On server-1 run
`sudo bash hosts/<host>/backup/install.sh` (it shows a diff, keeps a dated copy
of each replaced file, reloads systemd when a unit changed, enables the
timers, and finishes with a coverage check).

For server-2, update the reviewed checkout, run the common `check` operation,
copy `hosts/server-2/backup/units/infra-backup*` into `~/.config/systemd/user/`,
then run `systemctl --user daemon-reload`. After a verified capture and isolated
restore, enable `infra-backup.timer` and `infra-backup-maintenance.timer`.
User managers must have lingering enabled. Unit installation differs; the
capture, maintenance, status and recovery workflow is shared.

For repository operations use `backup/run.py --settings <settings-path> restic
<arguments>`; this works with either execution mode. It never prints credentials.
The restic password must also be escrowed outside the host. Do not disable old
backup jobs until the replacement has passed capture, upload and restore checks.
Retain old NAS/offsite repositories and recovery material after the transition.

## Failure alerts

The optional notifier below is specific to server-1. Server-2 reports failures
through the shared service status, journal and central logs.

`hosts/server-1/backup/alerts/` sends a Discord message through the owner's
existing Ping Me bot when a backup or maintenance run fails, or when one has
not succeeded for too long. It is deterministic Python (standard library, no
LLM) and posts only problems: no success, recovery or heartbeat messages.

- **Failure:** a drop-in adds `OnFailure=infra-backup-alert@%n.service` to each
  job. The message names the unit, result, exit status, last success and the
  `journalctl` command to run. It never includes log content.
- **Overdue:** `infra-backup-alert-check.timer` runs hourly and alerts when the
  last success is older than 36 hours (backup) or 8 days (maintenance), which
  also covers a disabled timer or a run that never finishes. On a fresh
  install the limit counts from the first check, so installing never alerts.
- **Last success** comes from a marker in `/var/lib/infra-backup-status/`,
  written by the same drop-in's `ExecStopPost=-...record-result`. The leading
  `-` means this bookkeeping can never change a job's result. systemd's record
  of the current boot is used as well.
- **Duplicates:** each job has one incident. After an alert, further failure
  or overdue alerts for that job are suppressed for 24 hours unless a run
  succeeds first; a success silently clears it. Failed deliveries share three
  attempts across hourly checks and failure hooks. Exhausting that budget starts
  the same repeat cooldown as delivery; a success clears it immediately.

The notifier runs as the account that owns the Ping Me configuration, so the
bot token stays in that account's private config and is never copied. Install,
optionally with `--test-message`, and roll back with:

```bash
sudo bash hosts/server-1/backup/alerts/install.sh --user <user> --helper /home/<user>/<path>/Personal-Ping-Me/scripts/ping_me.py
sudo bash hosts/server-1/backup/alerts/install.sh --uninstall
```

`--dry-run` shows the changes without making them. Tests run without root:
`python3 -m unittest scripts.tests.test_backup_alerts`. This only covers
failures the host can still report. A whole-host outage needs a checker on another
machine (not built yet).

## Restoring

Use `backup/run.py --settings <settings-path> restic snapshots` to choose a
single host snapshot, and `restic restore <id> --target <scratch> --verify` through
the same interface. The data format and acceptance steps below apply to both
hosts. Restore tests belong on isolated scratch storage on another machine when
possible; metadata checks alone do not prove application recovery. The shared
restore command requires an empty private target and mounts it writable in
user mode. Keep its configured metadata cache enabled for cloud restores.

Nextcloud's native exports are under the shared `staging/` root. Import PG globals
into a fresh cluster using a unique bootstrap admin, then database attributes
and the custom archive with owners/ACLs preserved and `ON_ERROR_STOP` enabled.
Restore its matching quiesced files, Redis RDB, config and encryption keys. Verify
original database-password authentication, user records and application file
create/read/delete before returning control to AIO. On a union filesystem, use
native scratch volumes with preserved owners/modes for application tests.


See RUNBOOK.md, "Restore a service".

Select one snapshot ID, restore to a private scratch directory with
`restic restore <id> --target <scratch> --verify`, and check staged database
and archive hashes against their manifests. Overlay the consistent SQLite
and quiesced copies with their saved ownership before starting applications.
Import native PostgreSQL dumps into fresh clusters, load the saved image
archive and apply its recovery image override, then start recovered applications
with private mounts and an internal Docker network. Publish no ports and attach no production Docker socket,
hardware, or live data. Verify database integrity, application startup, and
representative read/write actions; a successful upload alone proves none of
those. A full restore needs roughly the snapshot's size in free scratch space
(about 15 GiB in October 2026), and the scratch copy holds decrypted secrets,
so delete it when the drill is done.

The last full drill (2026-10-05) passed SQLite integrity, native PostgreSQL
imports, Forgejo clone/push and `git fsck`, Komodo's native restore, image
archive loading, SOPS decryption, and HTTP probes of the restored applications.
It ran in isolated containers on the host, not on a rebuilt one. Media, Loki log
history and caches are excluded by design.

A representative drill on 2026-10-29 validated the repaired image capture and
newer services from one freshly uploaded snapshot. All three required local
image IDs were present in the archive and loaded successfully. Seven restored
SQLite exports passed full integrity and numeric ownership/mode checks.
Email link helper passed settings decryption, authenticated settings reads and
encrypted writes using its recovered key. Career Ops passed startup, pipeline
reads and profile API writes after recreating its documented dependency links.
Kaneo passed native SQL import into an empty cluster, application startup and
database reads/writes. Containers had no outbound network access or published
ports; the drill did not test external integrations or a rebuilt host.
