# Kaneo

[Kaneo](https://github.com/usekaneo/kaneo) provides project and task management.
The bundled web/API image is pinned to release 2.47.1 through `KANEO_IMAGE`;
the runtime env file can further pin image digests. A dedicated PostgreSQL 16
container keeps its data separate from the shared cluster.
There are no cross-stack dependencies.

Caddy serves `KANEO_DOMAIN` over private tailnet HTTPS and proxies the same
origin to loopback port 8911. PostgreSQL has no published port. Native password
login is enabled; the first registered user becomes instance admin. Guest access
and subsequent public registration are disabled. Create the initial account in
the browser. SMTP, OAuth, Silo attachments, and external integrations are unset.

Runtime inputs live outside Git:

- `/opt/kaneo/compose.env`: image references and private network subnet.
- `/opt/kaneo/routing.env`: `KANEO_DOMAIN` for Caddy.
- `/opt/kaneo/postgres.env`: `POSTGRES_USER`, `POSTGRES_DB`, and generated password.
- `/opt/kaneo/app.env`: matching `POSTGRES_*` (host `kaneo-postgres`),
  `KANEO_CLIENT_URL=https://<KANEO_DOMAIN>`, a generated `AUTH_SECRET`,
  `DISABLE_GUEST_ACCESS=true`, and `DISABLE_REGISTRATION=true` (first-account
  bootstrap remains allowed). Keep env files mode 0600.
- `/opt/kaneo/postgres`: database cluster, owned by the image's PostgreSQL UID.
- `/opt/kaneo/data`: application data, UID 1001. Rendered assets and nginx
  temporary files use tmpfs at `/var/lib/kaneo` and are rebuilt at startup.

From `hosts/server-1`, run `docker compose up -d kaneo` to start/update only
Kaneo and its database; `docker compose restart kaneo` restarts the application.
Inspect `docker compose ps kaneo kaneo-postgres` and
`curl --fail http://127.0.0.1:8911/api/health`. Both containers have health checks,
automatic restart, bounded logs and resource limits. Alloy discovers their logs.

Nightly backups include `/opt/kaneo`: all four env files, application data,
runtime README and the active recovery plan at `/opt/kaneo/backup-plan.json`.
The live PostgreSQL directory is excluded; the native exporter captures the
dedicated cluster as `database-containers/kaneo-postgres.sql` beneath the
snapshot's staging directory. Source and shared routing retain their existing
repository backup coverage. No Silo/S3 object store is currently configured.
Restore matching config/data and import SQL into a fresh cluster with a unique
temporary bootstrap administrator before starting the application. Never copy
a live PostgreSQL directory as a backup. Include object storage and its keys
in coordinated capture if uploads are configured later.

See [MIGRATION.md](MIGRATION.md) for the exact recovery set, moving to another
host with downtime, and the Caddy/Tailscale Serve routing choices.
