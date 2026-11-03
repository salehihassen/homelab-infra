# Server-2 operations

## Identity and source of truth

Use the private SSH alias configured by the operator. The mapping from server-2
to a machine, service domains, network addresses and private source/data paths
belongs in ignored `local/site.md`, `host.local.yaml` and `/opt` runtime files.
Never paste live `docker compose config`, container inspection or env-file output
into a tracked document: all can contain secrets. Redact values when inspecting.

The host project lives at `hosts/server-2/compose.yaml`, with project name
`server`. Preserve this name, container names and external volumes. Retained
apps are Caddy, Flowise, n8n, the Nextcloud AIO controller, Open WebUI, Mock
Traveler and Personal Network. Alloy is the new log shipper. Commented legacy
services and retired storage are outside this deployment.

## Prepare and validate a checkout

Install the supported Docker/Compose runtime, Python/PyYAML, Restic and Git.
Enable `.githooks` for any checkout used to commit; install Gitleaks before
committing. Read the repository AGENTS instructions. Copy env examples into
private runtime files and restore data before starting applications. Keep env
files mode 0600 and secret directories mode 0700. Images in live interpolation
files must be pinned to a digest or a retained local image ID, except the AIO
controller, whose startup script rejects digest references. Preserve its
supported channel tag, verify the selected image ID before adoption, and use
`--pull never` during adoption. Snapshot metadata records its exact image ID.

From the repo root:

```bash
bash scripts/env-files.sh link server-2
bash scripts/env-files.sh check server-2
INFRA_HOST=server-2 bash scripts/validate-stack.sh all
bash scripts/check-private.sh
python3 backup/run.py --settings /opt/infra-backup/backup.env check
```

Validation uses CI's checksum-verified Compose version without replacing the
host plugin. It checks only example settings; validate the live project with
`docker compose config --quiet` from `hosts/server-2` as well. Public-host
validation has an explicit route allowlist and fixed loopback upstreams. It
does not relax server-1's private ingress or log-ingress restrictions.

## Adopt or update one application

Before adoption, record the running image ID, configured environment, named
volumes, bind sources and AIO child IDs in a private recovery directory. Keep
a tested recovery set and original deployment files. Do not combine adoption
with application upgrades, UID changes, data relocation or exposure changes.

From `hosts/server-2`:

```bash
docker compose config --quiet
docker compose up -d --no-deps --no-build --pull never <service>
docker compose ps <service>
docker compose logs --tail=50 <service>
```

Check the image ID and every persistent mount against the private baseline.
Preserve configured authentication/encryption keys. Check native health,
trusted HTTPS, existing login/data and relevant WebSocket/webhook behavior.
Use native app read/write tests on isolated recovery copies when production
credentials or external side effects make a direct test inappropriate. Stop
on a failed check before adopting another stateful service. Never use a
project-wide `down`, `down -v`, blanket volume cleanup or orphan removal.

For upgrades, take a fresh backup, read upstream migration requirements, select
the new digest in the private image input, recreate only the affected service,
and repeat checks. Build Mock Traveler deliberately from its private source path;
retain and select its resulting exact image ID. Do not use a moving local tag
as recovery identity. Dependency containers are not implicitly updated.

The old Compose entrypoint may be retained as a forwarding wrapper to this
checkout after adoption. Preserve its original files and project-directory
context privately for rollback. Do not continue maintaining two independent
active deployment definitions.

## Nextcloud AIO boundary

Only the master container belongs to this Compose project. Keep its name and
`nextcloud_aio_mastercontainer` external volume unchanged. Preserve its data-dir
setting, API socket and controller configuration. AIO owns all child creation,
updates, resource settings and its private network; never add those children as
Compose services. Confirm child container IDs remain unchanged during controller
adoption, then check their health and `/status.php`.

The controller checks browser Fetch Metadata headers. A plain CLI request can
correctly receive 403; use `curl --fail -H 'Sec-Fetch-Site: none'
"https://<AIO_ADMIN_DOMAIN>/login"` when checking its trusted HTTPS login route.
Do not disable that protection to make a health probe pass.

The Docker socket grants administrative power even when mounted read-only.
The controller's local self-signed HTTPS upstream is the only proxy TLS-verifier
exception. User-facing HTTPS still uses trusted certificates. Existing app
backends and the controller's direct port are private runtime listener choices;
audit clients before narrowing a legacy listener.

## Daily operation and logging

```bash
docker compose ps
docker stats --no-stream
curl --fail http://127.0.0.1:12345/-/ready
```

Alloy's HTTP API is loopback-only. Docker and host/user-unit journal logs are
sent to the existing private authenticated Loki write endpoint. Find records
in Grafana with `{host="server-2"}`; narrow by `container`, `compose_service` or
`unit`. Backup success is logged as `backup_complete`; failures as
`backup_failed`. Check for missing success over the expected daily window.
No read credentials or raw Loki listener are deployed here. Writer-password
rotation must update the mode-0600 file and preserve its container-readable
numeric owner, followed by restarting only Alloy. Its state directory is
disposable; replay older than 168 hours is discarded.

Use the actual persistent or volatile journal directory in `JOURNAL_DIR`; it
must exist after reboot. Log access and Docker socket access are trusted admin
capabilities. Bound Docker log rotation applies to managed containers; AIO
retains control of its own children's logging configuration.

## Backups and recovery

Both hosts use the [shared backup playbook](../../docs/backups.md) for
schedules, capture, maintenance, status and isolated restore acceptance.
Server-2 uses `/opt/infra-backup/backup.env` and lingering user systemd units;
its Docker helper supplies root file access without interactive sudo. Only
Nextcloud's coordinated capture adapter is application-specific. AIO retains
ownership of its children.

## Rollback

Before data/schema changes, restore the previous deployment definitions/image
inputs and recreate only the affected service with the original data mounts.
If an update or new instance accepted writes, carry the latest database and
matching files back before switching routing. Never create two independent
writable copies. Keep the original recovery set and deployment evidence through
the acceptance window. Adoption does not authorize deleting retired data.
