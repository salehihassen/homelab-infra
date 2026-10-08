# Backups

Each host makes one encrypted, deduplicated restic snapshot per night
(`infra-backup.timer`, 03:15–03:45) to Backblaze B2 through rclone.
`infra-backup-maintenance.timer` prunes weekly (14 daily, 8 weekly, 12 monthly)
and checks the repository.

What gets backed up is declared next to the thing being backed up:

- `hosts/<host>/<stack>/backup.yaml` for each stack,
- `hosts/<host>/backup/host.yaml` for everything that belongs to no stack
  (host configuration, backup tooling, retired data),
- the untracked `hosts/<host>/backup/host.local.yaml` for paths that identify
  the owner or machine (home directories, source repositories, other apps);
  template: `host.local.yaml.example`.

Settings (restic repository, credentials paths, restic host tag, checkout
location and host alias) come from `/etc/infra-backup/backup.env`; template:
`hosts/<host>/backup/backup.env.example`.

The runner (`hosts/<host>/backup/infra-backup`, installed to `/usr/local/sbin`)
reads these declarations from this repository at run time. It **fails** instead
of uploading a partial snapshot when:

- a bind mount, `env_file` or secret file used by any Compose service is
  neither backed up nor excluded with a reason,
- the shared PostgreSQL cluster contains a database no stack declares, or a
  declared one is missing,
- a declared path or SQLite database does not exist,
- an `offline` path is in use by a running container.

## Declaration format

```yaml
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

Use `quiesce` only when a stack's database and files must agree at one point
in time, and list every container that writes to them. Forgejo is the only
such stack today (repositories on disk, metadata in Postgres); the pause lasts
about a second.

## Snapshot layout

Live paths keep their host paths. Consistent copies live under the staging
directory, which is part of every snapshot:

```
/var/lib/infra-backup/staging/
  plan.json, excludes.txt            # what this run decided to back up
  databases/postgres/globals.sql     # roles and other cluster globals
  databases/postgres/<db>.dump       # pg_dump --format=custom per database
  database-files/<source path>       # SQLite online copies, mirroring the host
  quiesced/<source path>             # files copied while their writers were paused
  database-containers/<name>.sql     # other running Postgres containers (demos)
  archived-state/offline-state.tar   # `offline` paths
  images/local-images.tar           # running locally built stack images
  inventory/                         # containers, mounts, packages, disks
```

## Checks you can run without root

```bash
python3 hosts/server-1/backup/infra-backup-declared.py check --live
S=$(mktemp -d) && python3 hosts/server-1/backup/infra-backup-declared.py plan "$S" && cat "$S/plan.json"
```

`check` also runs in `install.sh` and at the start of every backup.

To take a backup now and confirm the service succeeded:

```bash
sudo systemctl start infra-backup.service
systemctl show infra-backup.service -p Result -p ExecMainStatus
journalctl -u infra-backup.service -n 25 --no-pager
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
SQLite exports retain the live database's numeric owner and permissions;
those values are also recorded in `inventory/sqlite-manifest.json`. Restore
that metadata rather than guessing the owner from the containing directory.

Env files are not in Git, so the snapshot is their only copy off the host.
Keep the restic password and B2/rclone credentials outside the host as well: copies inside
an encrypted snapshot cannot bootstrap access to that snapshot.

## Changing the runner

Edit the scripts or units in `hosts/<host>/backup/`, then
`sudo bash hosts/<host>/backup/install.sh` (it shows a diff, keeps a dated copy
of each replaced file, reloads systemd when a unit changed, enables the
timers, and finishes with a coverage check).

## Restoring

See RUNBOOK.md, "Restore a service".

Select one snapshot ID, restore to a private scratch directory with
`restic restore <id> --target <scratch> --verify`, and check staged database
and archive hashes against their manifests. Overlay the consistent SQLite
and quiesced copies with their saved ownership before starting applications.
Import native PostgreSQL dumps into fresh clusters, load the saved image
archive, then start recovered applications with private mounts and an internal
Docker network. Publish no ports and attach no production Docker socket,
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
