# Retired server-1 Kaneo

Kaneo now runs only on [server-2's public K3s cluster](kubernetes/kaneo.md).
The server-1 Compose application and its dedicated PostgreSQL 16 container are
retired. Their Compose definitions, network, Caddy site and routing env input,
env-file mappings, inventory entry and nightly stack backup declaration are
removed. The old private hostname no longer serves Kaneo.

The K3s instance was created independently. No data, accounts, credentials or
attachments from the old instance are imported into it by this retirement.
Its deployment, public route, database, object storage and backups continue
under the existing server-2 configuration.

## Retained recovery state

Original `/opt/kaneo` configuration, credentials, application data and the
cleanly stopped PostgreSQL directory remain on server-1. A final native SQL
export, original deployment inputs and a private retirement record are retained
outside Git; their locations are in untracked `local/site.md`. No runtime files,
volumes, databases or historical Restic snapshots are deleted. The runtime
recovery plan is marked disabled so it no longer claims active nightly coverage.
The retained files are for recovery, not an active deployment.

Future server-1 backups no longer capture this retired stack or export its
dedicated database. Server-2's existing K3s recovery sets protect the active
instance independently. Retaining old files or historical snapshots does not
establish new off-host backup coverage for the retired instance.

## Recover the old instance

Recover the pre-retirement stack definitions and env-file mapping from Git or
the private retained deployment copy. Preserve the original env files and
`AUTH_SECRET`, numeric owners and permissions; do not regenerate credentials.
Restore a historical native PostgreSQL export or the final SQL export into an
empty matching-major cluster with a unique temporary bootstrap administrator,
using `psql -v ON_ERROR_STOP=1`. Restore matching application files and settings.

Validate Compose and backup coverage before starting a recovered service. Keep
it isolated from outbound integrations and verify health, login and data before
intentional promotion. Use a separate database and route; never import the old
SQL into the active K3s database or point both applications at the same database.
The old migration instructions remain available in pre-retirement Git history.
