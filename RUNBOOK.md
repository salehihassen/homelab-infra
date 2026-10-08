# Runbook

Commands assume the repository checkout as the working directory unless they
`cd` first. Real host names, domains, repository locations and the restic
repository for this deployment are in the untracked `local/site.md`.

## Where things are

| What | Where |
| --- | --- |
| Deployment definitions (this repo) | A checkout on each host; host directories use aliases (`hosts/server-1`) |
| Application source | Each app's own repository; image references and source paths are set in the stack's `.env` |
| Runtime data and secrets | `/opt` on the host (see `/opt/README.md`) |
| Env files | Only under `/opt` and in restic snapshots; repo symlinks mapped in `hosts/<host>/env-files.tsv` |
| Domains | `PRIVATE_ZONE` in `caddy/.env`; each service's `<NAME>_DOMAIN` in its stack's `routing.env` |
| Backups | `/etc/infra-backup/backup.env` (restic repository, password and rclone config paths, host tag) |
| DNS | Each `<name>.PRIVATE_ZONE` resolves to the host's Tailscale addresses (a wildcard record covers every name) |

## Secrets

Env files and secrets never enter Git. They live under `/opt` on the host, and
the nightly restic backup is their only other copy, so the restic password and
the rclone/B2 credentials must live in a password manager: they are the only
way into the backups when the host is gone. Save the original rclone config
and `/etc/cryptsetup-keys.d/services.key` as private attachments alongside the
restic password and repository address. Verify you can retrieve them from
another device. Keys stored only inside the encrypted backup cannot unlock
that backup after the host is lost.

```bash
bash scripts/env-files.sh check    # every runtime env file exists and is linked
bash scripts/env-files.sh link     # recreate the repo symlinks (new clone)
```

## Day-to-day

```bash
cd hosts/server-1
docker compose up -d <service>            # apply a compose change
docker compose up -d --build <service>    # rebuild a local image (bump its tag first)
docker compose exec caddy /bin/sh /etc/caddy/render-sites.sh caddy reload --config /etc/caddy/Caddyfile
bash ../../scripts/validate-stack.sh      # before committing
```

Never `docker compose down` the whole project and never pass `-v`.

### Network subnets

Each stack defines its own networks in its `compose.yaml`, with required
subnet variables from its local `.env`. The main `/opt/.env` contains only
shared `TZ`; stack-specific images, source paths, devices, connection
settings, static IPs, and secrets live with their owning stacks. The root
include loads only that stack's `.env` and, when applicable, `routing.env` for
interpolation. Files are ignored symlinks to runtime settings under `/opt`,
mapped in `env-files.tsv`; example templates are tracked beside each stack.
Ordinary commands and Komodo use only `compose.yaml`:

```bash
docker compose -f compose.yaml config -q
```

The current allocation is `10.66.0.0/24` for observability, `.1` for home
automation, `.2` for Postgres, and `.3` for Komodo. `.4` through `.7` are reserved
and have no Docker networks. The other stack-private networks start at `.8`.
CPA's three subnet variables live in `ai-gateway/.env`; Forgejo CI's lives in
`forgejo/.env`. Their existing ranges and static IPs are preserved.
Keep every subnet disjoint from the other Docker networks and host routes;
automatic allocation can otherwise consume a subnet needed by a fixed network
before that network is recreated during startup.

`include.env_file` supplies interpolation values for the stack definitions;
service-level `env_file` instead sets variables inside containers. Shell and
host env values take precedence over stack-local defaults, so keep the main
env file limited to shared settings. Validation uses `STACK_ENV_FILE=.env.example`
and `STACK_ROUTING_ENV_FILE=routing.env.example` to load tracked examples;
ordinary commands use `.env` and `routing.env`.

### Optional routing and removing a stack

`routing.env` contains only Caddy inputs, including ingress auth hashes when
needed, and is separate from application credentials. Caddy lists these files
with `required: false`. Its startup renderer imports only sites whose referenced
env values are all nonempty. A missing routing file or variable skips that
stack's sites and logs the missing variable names; other routes stay available.
Application/database credentials and session secrets are not passed to Caddy.
The host hostname serves a Caddy identity response. AI policy traffic uses the
gateway's `POLICY_PROXY_DOMAIN`; Caddy has no AI gateway startup dependency.

To retire an independent stack, remove its include block from the root Compose
file. Archive/delete its configuration directory when desired; retained files
and data can stay for recovery. Also remove its inventory, env-file mapping
and backup declaration only when their retention is no longer wanted. Caddy's
optional env entries may remain when their files are absent.

Native Compose still requires configuration for an included stack. A missing
mandatory stack setting fails validation; `required: false` does not disable a
service. Remove unavailable stacks' includes and address declared dependents
before startup. Removing a provider such as Postgres or the AI gateway requires
handling its consumers. There is no automatic skip/startup helper.

After changing `routing.env`, recreate Caddy to update its container environment.
For site-file edits, the reload command above regenerates the active imports.
Network subnet changes require recreation of the affected networks.

### Add a service

1. Create `hosts/<host>/<stack>/` with `compose.yaml` (`x-stack.requires`),
   `README.md`, `backup.yaml`, and `site.caddy` if it has a web UI.
2. Add it to `hosts/<host>/compose.yaml` and `inventory.yaml`. Define each network
   in the stack's `compose.yaml` using a required subnet variable from its
   `.env` + `.env.example`. Register new runtime env files in `env-files.tsv`
   and the stack's `backup.yaml`.
3. For a hostname: add `<NAME>_DOMAIN` to the stack's `routing.env` +
   `routing.env.example`, add an optional `env_file` entry in
   `hosts/<host>/caddy/compose.yaml`, and include the routing env file in that
   stack's root include. With a wildcard DNS record and the wildcard
   certificate, no DNS or certificate change is needed.
4. `bash scripts/validate-stack.sh`, `python3 hosts/<host>/backup/infra-backup-declared.py check --live`,
   `bash scripts/env-files.sh check`, commit, `docker compose up -d <service>`.

## Komodo

At `https://KOMODO_DOMAIN` (see `hosts/server-1/komodo/README.md` for first
login and 2FA). The whole `opt` project is one Komodo stack in "files on
server" mode (run directory `<checkout>/hosts/server-1`, file `compose.yaml`).
Komodo parses services from `docker compose config` during a deploy, so after
adding a stack, run one Deploy from Komodo (or `docker compose up -d` then
Deploy) to refresh its service list. Its "Global Auto Update" schedule is
disabled; image updates come from Renovate PRs.

When the Git remote exists:

1. Add a read-only deploy key for the repo; register it in Komodo as a Git
   provider account (Settings -> Providers).
2. Point Periphery's root at a clone location it can write, or keep "files on
   server" and let Komodo run a `git pull` procedure before deploys.
3. For another server: install Periphery with an outbound connection to
   `wss://KOMODO_DOMAIN` (no inbound port), and add `hosts/<alias>/` here.

Change Komodo's own services from the CLI, never from the Komodo UI.

## Backups

The runner, its scripts and systemd units are in `hosts/<host>/backup/`; the
format of the declarations is in `docs/backups.md`. To install or update:

```bash
sudo install -d -m 700 /etc/infra-backup
sudo install -m 600 hosts/server-1/backup/backup.env.example /etc/infra-backup/backup.env  # first time; then edit
sudo bash hosts/server-1/backup/install.sh --dry-run
sudo bash hosts/server-1/backup/install.sh
```

`install.sh` installs the scripts and units, enables `infra-backup.timer` and
`infra-backup-maintenance.timer`, and runs a live coverage check.

## Rebuild a host from bare metal

1. Install Ubuntu, Docker Engine + Compose v5.5.1, restic, rclone, Tailscale.
   Log in to Tailscale; confirm `tailscale ip` matches the DNS records for
   the host's names (update them if not).
2. Restore the rclone config and restic password from the password manager,
   then the latest snapshot to a scratch directory (values from `local/site.md`
   or the password manager entry):
   ```bash
   export RESTIC_REPOSITORY=<repository> RESTIC_PASSWORD_FILE=<password file> RCLONE_CONFIG=<rclone config>
   sudo -E restic snapshots --host <backup host>
   sudo -E restic restore <snapshot-id> --target /restore --verify
   ```
3. Put files back at their original paths: `/opt/**`, the repository
   checkouts and other paths from `backup/host.local.yaml`,
   `/etc/infra-backup`, `/etc/systemd/system/infra-backup*`,
   `/usr/local/sbin/infra-backup*`, and the user systemd units. Live database
   files were not backed up raw; restore them in step 5.
4. Recreate the env symlinks (the env files themselves came back with `/opt`):
   ```bash
   bash scripts/env-files.sh link
   bash scripts/env-files.sh check
   ```
   Then log in to the container registry, so private images can be pulled.
   Docker credentials are not in the backup; use the read-only token
   (`read:packages` scope only) from the password manager:
   ```bash
   docker login ghcr.io -u <github user>   # paste the token at the prompt
   cd hosts/server-1 && docker compose pull --ignore-buildable
   ```
   Pulling every image now surfaces an expired token or a missing image
   before any service is stopped or started. Komodo needs the same token as
   a registry account (Settings -> Providers) to deploy private images.
5. Before starting application writers, restore staged state from
   `/restore/var/lib/infra-backup/staging/`:
   - Load `images/local-images.tar` with `docker image load --input`.
   - Shared Postgres: see `hosts/server-1/postgres/README.md`. Use a unique
     temporary bootstrap administrator in an empty cluster, disable init
     scripts during import, restore globals with `ON_ERROR_STOP`, then each
     `<db>.dump` with `pg_restore --exit-on-error`. This avoids collisions with
     restored roles.
   - SQLite: use `inventory/sqlite-manifest.json` to find each consistent
     `database-files/<path>` export. Copy it to its original path with its
     service stopped, remove old WAL/SHM/journal companions, restore the
     recorded owner and mode, and run `PRAGMA integrity_check` before starting
     the writer.
   - Forgejo: copy `quiesced/opt/forgejo/data` with numeric owners and permissions
     preserved alongside its database from this same snapshot.
   - Komodo: initialize its pinned DocumentDB/FerretDB services, then run the
     Core image with `km` as its entrypoint to import before Core's normal
     startup creates default resources. Supply a private CLI config with
     `[database_target]` address/username/password/db_name for the new instance;
     use `km --config-path <file> database restore --yes --restore-folder
     <dated-folder>` and mount the recovered `/opt/komodo/backups` at `/backups`.
     Start Core only after import. Keep Periphery stopped until recovered Core
     state has been reviewed. Native SQL dumps are also available under
     `database-containers/`; importing them needs extension bootstrap/grant
     handling, so prefer the tested native application export procedure.
6. Host applications outside Compose need their runtime dependencies
   reinstalled; dependency/build directories are intentionally excluded.
   Restore their user units and consistent SQLite exports before starting
   them. Re-enroll Tailscale rather than copying another host's identity.
7. `docker compose up -d` for everything else, then `docker compose ps` and
   check each `https://<name>.PRIVATE_ZONE`.
8. Re-enable timers: `sudo systemctl enable --now infra-backup.timer infra-backup-maintenance.timer`;
   `systemctl --user enable --now` the `*-export.timer` units.

## Restore a single service

1. `docker compose stop <services>` (every writer listed in the stack README).
2. Select one snapshot ID and restore its paths and
   `/var/lib/infra-backup/staging` to a private scratch target with `--verify`.
   Copy back only the chosen service's files with numeric owners preserved.
3. Overlay its consistent SQLite/quiesced copies and restore its native
   database dumps as in step 5 above. Use files and databases from the same
   snapshot. For a single PostgreSQL database, preserve the existing cluster
   and other databases; do not replace the whole data directory.
4. `docker compose up -d <services>`.

## Practice restore

Restore one real service's database and files together into an isolated
scratch project, without touching production (restic settings exported as in
the bare-metal rebuild):

```bash
sudo -E restic restore latest --host <backup host> --target /tmp/restore-drill \
  --include /var/lib/infra-backup/staging/databases/postgres \
  --include /opt/email-assist
docker run -d --name drill-pg --network none -e POSTGRES_PASSWORD=drill \
  --label infra.recovery-drill=true pgvector/pgvector:0.8.6-pg18-trixie
docker exec -i drill-pg psql -U postgres < /tmp/restore-drill/var/lib/infra-backup/staging/databases/postgres/globals.sql
docker exec -i drill-pg createdb -U postgres -O email_assist email_assist
docker exec -i drill-pg pg_restore -U postgres -d email_assist --no-owner --role=email_assist \
  < /tmp/restore-drill/var/lib/infra-backup/staging/databases/postgres/email_assist.dump
docker exec drill-pg psql -U postgres -d email_assist -c '\dt'
docker rm -f drill-pg && sudo rm -rf /tmp/restore-drill
```

The `infra.recovery-drill=true` label keeps the nightly backup from dumping
the drill container. A full restore needs roughly the snapshot's size in free
scratch space and leaves decrypted secrets behind: delete it afterwards.
