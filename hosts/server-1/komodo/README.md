# komodo

Komodo v2 (`KOMODO_DOMAIN`): one web UI for the Compose stacks on this host,
with the other servers to follow. Tailnet-only through Caddy; local accounts only,
sign-up disabled.

- **Core** keeps its state in **FerretDB** on its own Postgres + DocumentDB
  container (`komodo-postgres`), separate from the shared `postgres` stack.
- **Periphery** (server `KOMODO_SERVER_NAME`) connects outbound to Core over the stack network
  and drives Docker through the socket. It sees `/opt` and the checkout
  root (`KOMODO_REPOS_DIR`) read-only at their host paths, so Compose can resolve env files, secrets and
  build contexts. Web terminals are disabled.
- **Secrets:** `/opt/komodo/komodo.env` (database password, JWT and webhook
  secrets, initial admin user and its password). Core/Periphery key pairs are generated in
  `/opt/komodo/keys` on first start.
- **Backup:** Komodo's built-in "Backup Core Database" procedure writes dated
  exports to `/opt/komodo/backups`. The nightly runner refreshes these exports
  and takes a native DocumentDB/PostgreSQL dump before uploading them. The live
  database directory is excluded.

## The `opt` stack in Komodo

The whole host project is registered as one Komodo stack in "files on server"
mode: run directory `<checkout>/hosts/server-1`, project name
`opt`. Komodo shows every container, its logs and state, and can redeploy.
Once the private GitHub remote exists, switch the stack to the Git repository
with a read-only deploy key (RUNBOOK.md, "Komodo").

Komodo is itself part of `opt`: change its own services with
`docker compose up -d komodo-...` from the CLI, never from the Komodo UI, so a
deploy cannot stop the agent that is running it. Wait about a week after a
Komodo release before upgrading (Renovate's `minimumReleaseAge`).

## First login

1. Open `https://KOMODO_DOMAIN` and sign in with `KOMODO_INIT_ADMIN_USERNAME` and
   `KOMODO_INIT_ADMIN_PASSWORD` from `/opt/komodo/komodo.env`.
2. Change the password, then enable two-factor authentication in the user
   settings.
3. Remove `KOMODO_INIT_ADMIN_*` from `/opt/komodo/komodo.env`.
