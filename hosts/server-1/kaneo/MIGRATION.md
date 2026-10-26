# Kaneo backup and migration

Nightly backups include `/opt/kaneo`, excluding only its live PostgreSQL data
directory. The dedicated database is exported natively into snapshot staging.
The active recovery plan is `/opt/kaneo/backup-plan.json`; the old path at
`/opt/tmp/kaneo-backup-plan.json` is a compatibility symlink. A passing coverage
check accepts explicit exclusions; check the actual snapshot and restore it
before claiming recoverability.

## Recovery set

Preserve these together:

- `/opt/kaneo/{app,postgres,compose,routing}.env`: database credentials,
  `AUTH_SECRET`, app URL, image digests and network settings. Preserve all future
  encryption keys, including `NOTIFICATION_SECRET_ENCRYPTION_KEY` if configured.
  Keep credentials mode 0600 and transfer over an encrypted connection.
- A native PostgreSQL export from `kaneo-postgres`: accounts, password hashes,
  projects, tasks, settings and other application records.
- `/opt/kaneo/data` with numeric ownership preserved (currently UID 1001/GID 0).
  Include it even when empty; it is the app's persistent bind mount.
- This directory's deployment files, the host include and env mapping, proxy
  configuration, runtime README, and the infra commit identifying them.
- Any Silo/S3 objects and their configuration/keys if uploads are enabled later.
  No object store is configured in this deployment. The local `data` mount does
  not replace object-storage backups.

Do not back up a running `/opt/kaneo/postgres` directory as ordinary files.
Use a native database export instead. Rendered assets, nginx temporary files,
logs, and caches are disposable. Reuse the recorded image versions for recovery;
upgrading is a separate operation.

The database has no backup opt-out label. The native exporter recognizes both
tag and digest image references and writes its full
SQL export under `database-containers/kaneo-postgres.sql` in the snapshot staging
directory. Do not add `kaneo` to the declaration's `postgres` list: that list
addresses the separate shared cluster. Test recovery before claiming coverage.
Restore full-cluster SQL into an empty isolated cluster with a unique temporary
bootstrap administrator, following the repository's PostgreSQL restore procedure
to avoid duplicate bootstrap roles. If file/object state needs the same recovery
point as the database, stop all writers or use coordinated snapshots during capture.

## Move with downtime

1. Prepare Docker, storage, Tailscale access and the intended proxy on the new
   host. Pull the same Kaneo and PostgreSQL image digests. Check architecture,
   available RAM/disk, network ranges and loopback port 8911. Deploy only this
   stack; importing the source host's root Compose file would deploy unrelated
   applications. A separate Compose project can use this stack's `compose.yaml`
   with `--env-file /opt/kaneo/compose.env` and a new project name.
2. Begin maintenance. Disable the old route or show a maintenance response,
   then stop only `kaneo`, leaving `kaneo-postgres` running for the export.
   If uploads are enabled by then, wait for outstanding signed upload URLs to
   expire (default five minutes) before capturing storage too.
3. Create a private transfer directory (`umask 077`). Run
   `docker exec kaneo-postgres pg_dump -U kaneo -d kaneo -Fc > database.dump`.
   Check its exit status and `pg_restore --list`. Copy the env/config files and
   matching application data, plus object storage if applicable. Use a native
   dump rather than copying the live PostgreSQL mount. Database dumps contain
   private user data and credentials and require the same protection as secrets.
4. Transfer over SSH/Tailscale. On the destination, restore env files and app
   data with their original permissions. Initialize only the destination's
   PostgreSQL container with the recovered `postgres.env`; it creates the same
   `kaneo` role and empty database. Keep the application stopped. Restore the
   custom archive with `pg_restore -U kaneo -d kaneo --no-owner --no-privileges
   --exit-on-error`. This single-database method does not require reimporting
   globals into the bootstrap role. Account for any custom database roles if
   the installation evolves. Inspect and stop on any restore error.
5. Set the destination URL and proxy trust settings. Keep `AUTH_SECRET` and any
   encryption keys unchanged. The bundled image derives its API URL from
   `KANEO_CLIENT_URL`; leave `KANEO_API_URL` unset. Environment changes require
   container recreation. Check `TRUSTED_PROXIES` against the new proxy/Docker
   gateway instead of copying source-host addresses blindly. Preserve native
   login and WebSocket forwarding. Keep outbound integrations isolated during
   rehearsal; restored DB records can contain credentials and queued actions.
6. Start the destination app. Verify health, existing login, representative
   projects/tasks, a test write, live updates, and attachments when configured.
   Switch the route/DNS once ready. If keeping the hostname, change both A and
   AAAA records. Leave the old app stopped so DNS caching cannot produce two
   independently writable instances. DNS propagation can extend the downtime.
7. Configure and verify backups/logging on the destination, then remove or
   retire the old stack declaration and runtime only after the rollback window.
   Running the new app on another host does not transfer the source host's backup
   coverage. Keep the old deployment intact and stopped until acceptance.

Rollback before the destination accepts writes is to stop it, restore the old
route/DNS, and start the old app. After new writes, migrate the latest database
and matching files back or explicitly accept losing those writes. Never run
both independent copies as active writers.

## Destination routing choices

On a VPS, a public IP does not require making Kaneo public. To retain private
access, bind Caddy only to the VPS's Tailscale addresses, keep the app on
`127.0.0.1:8911` and PostgreSQL unpublished, and repoint the service's A/AAAA
records to those tailnet addresses. Reissue the needed certificate on the VPS;
sharing the source host's wildcard private key is unnecessary. Keep DNS API
credentials outside Git and scoped to the required zone where possible.
Public access would instead use the intended public HTTPS listener and public
DNS records, with native auth and disabled guest/public registration preserved.
Check public IPv4 and IPv6 listener/firewall behavior separately.

On a destination using Tailscale Serve, first inspect existing Serve routes and
listeners. If the root HTTPS route is free, `tailscale serve --bg
http://127.0.0.1:8911` provides persistent private HTTPS using the device's
`<device>.<tailnet>.ts.net` hostname. Set `KANEO_CLIENT_URL` to that origin and
recreate Kaneo. Serve's managed HTTPS names are restricted to the tailnet domain;
a CNAME for an existing custom hostname alone does not provide a matching
certificate. Keep Caddy or another certificate-managing proxy for a custom URL.
Prefer a dedicated hostname over putting Kaneo under an untested URL subpath.
Serve adds identity headers but does not replace Kaneo's native authentication.
Funnel is the separate public-access feature. Do not enable Serve on the source
host: its existing Caddy owns HTTPS on those tailnet addresses.

Sources: [Kaneo backups](https://kaneo.app/docs/core/operations/backups),
[Kaneo proxy configuration](https://kaneo.app/docs/core/reverse-proxy/nginx),
[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve),
[Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve).
