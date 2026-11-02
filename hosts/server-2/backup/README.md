# Scheduled recovery

The application host runs `infra-backup.service` as a lingering user service.
Docker membership gives it administrative access; it is not an unprivileged
security boundary. Python 3, PyYAML, Docker and a verified Restic 0.18.1 binary
are required. Read settings from `/opt/infra-backup/config.json` and
`backup.env`; copy the examples and keep them mode 0600.

Each stack's `backup.yaml` declares runtime paths and `volumes` lists explicit
Docker volume names. Private existing bind/source paths belong in ignored
`host.local.yaml`. Coverage checks reject undeclared active mounts, missing
applications, unpinned application images and newly introduced AIO volumes.
The AIO controller requires its supported channel tag rather than a digest;
its exact running image ID is recorded with every capture. Commented or
retired services are not scheduled backup inputs.

`infra-backup.py backup` creates one encrypted Restic snapshot per stack, plus
host configuration. Stateful writers are stopped only for their own capture
and always restarted, including on errors and termination. Nextcloud also enters
maintenance while its own writers are stopped; PostgreSQL stays running for
native database/globals/ownership/grant exports, and Redis saves its RDB. Its
live PostgreSQL volume is never copied. SQLite uses native, integrity-checked
exports with original owners/modes and matching files. Failed staging remains
for inspection; a retry fails closed until that staging is recovered or moved.
Snapshots record exact running image identities. The locally built demo and
the application distributed through a private registry also include image
archives, so recovery does not depend on access to those image sources.

The private destination runs `receiver-compose.yaml`: TLS, password
authentication, per-user repository scope and append-only remote access. The
actual listener address, storage location, certificate, key and authentication
hash are private runtime inputs. It has no public listener. Restic's separate
repository password provides client-side encryption; escrow it independently.

The destination owner runs `receiver-maintenance.py` weekly through a user cron
entry. Retention groups by host and tags: fourteen daily, eight weekly and twelve
monthly snapshots. Local maintenance can prune; append-only application-host
credentials cannot delete snapshot data. Weekly checks read a ten-percent data
sample. Status is saved separately from the encrypted repository.

A separate gateway user timer runs `mirror.py` to copy these snapshots into a
distinct offsite Restic repository. Its B2/rclone account credentials stay on the
existing gateway; they are not copied to the application host. The destination
uses its own password and chunking parameters, and the original gateway repository
is never modified. The mirror shares the gateway backup lock, preventing overlap.

Install only `infra-backup.*` units on the application host. Install only the
`server-2-backup-mirror*` units on the gateway. Unit environment files select the
checkout location; tracked units contain no private home path. Timers use each
machine's configured local time. Run a manual backup and offsite mirror and
check receipts before enabling timers. Copy-only offsite snapshots are verified
with repository metadata checks; weekly maintenance additionally reads data.

## Snapshot contents

Select snapshots by host `server-2` and tag `stack:<stack>`. Host configuration
uses `stack:host`. Paths retain their source locations. Each stack also includes
`/opt/infra-backup-state/staging/<stack>/` with:

- `inventory.json`: captured containers, mounts, environment and paths; sensitive.
- `images.json`: exact image IDs and registry digests.
- `sqlite.json` and `database-files/<absolute source path>`: native SQLite
  exports, numeric owners/modes and per-table counts.
- Nextcloud: `database.dump`, `globals.sql`, `database-attributes.sql`,
  `postgres-counts.sql` and `postgres-counts.txt`.
- Locally built/private-registry applications: `local-image.tar`.

Restic restore verification checks file contents. Test native database import
and application behavior separately; metadata checks alone do not prove recovery.
On a NAS, use native filesystem scratch volumes for application tests. A union
filesystem can enforce source ancestor permissions for a service UID even when
the restored directory is bind-mounted into a container. Preserve captured
owners and modes rather than changing production data to accommodate a drill.

See [the playbook](../PLAYBOOK.md) for operation, restore, rollout and rollback.
