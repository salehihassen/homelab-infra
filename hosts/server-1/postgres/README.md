# postgres

Shared PostgreSQL 18 (+ pgvector) cluster, pgAdmin (`PGADMIN_DOMAIN`), and the
stopped 12.3 cluster kept for reference (`--profile deprecated`).

- **Provides:** network `postgres_network` (`opt_postgres_network`) and service
  `postgres`. Clients join the network, depend on `postgres` being healthy, and
  connect to `postgres:5432` with their own role and database.
- **Consumers:** quotes (`ductape`), email-assist (`email_assist`), forgejo
  (`forgejo`). pgAdmin joins the same network.
- **Data:** `/opt/postgresql/data18` (live cluster, never copied raw),
  `/opt/postgresql/initdb18`, `/opt/postgresql/*.env`, `/opt/postgres.env`,
  `/opt/pgadmin`.
- **Backup:** `pg_dumpall --globals-only` once, then `pg_dump --format=custom`
  per database, each declared by the stack that owns it. The backup fails if
  the cluster has a database no stack declares.

## Adding a database for a new stack

1. Create a role and database in pgAdmin or `docker exec -it postgres psql`.
2. In the new stack: join `postgres_network`, add
   `depends_on: {postgres: {condition: service_healthy}}`, list `postgres`
   under `x-stack.requires`, and add the database name to its `backup.yaml`
   under `postgres:`.

## Restore

Dumps are in the restic snapshot under
`/var/lib/infra-backup/staging/databases/postgres/`.

For a full replacement cluster, use a fresh directory and a unique temporary
administrator. Starting the ordinary Compose service first creates roles from
its init scripts that collide with the saved globals. Run this from a root
shell after restoring a chosen snapshot to `/restore`. The example uses
scratch storage; use the new, empty `/opt/postgresql/data18` only for a
replacement-host recovery. `mkdir` deliberately rejects an existing directory.

```bash
PG_RESTORE_DATA=/restore/pg-recovery-data
PG_RESTORE_ADMIN="restore_admin_$(openssl rand -hex 4)"
PG_DUMPS=/restore/var/lib/infra-backup/staging/databases/postgres
mkdir -m 700 "$PG_RESTORE_DATA"
docker run -d --name pg-recovery --network none \
  --label infra.recovery-drill=true \
  --mount "type=bind,src=$PG_RESTORE_DATA,dst=/var/lib/postgresql" \
  -e "POSTGRES_USER=$PG_RESTORE_ADMIN" -e POSTGRES_DB=postgres \
  -e "POSTGRES_PASSWORD=$(openssl rand -hex 32)" \
  pgvector/pgvector:0.8.6-pg18-trixie
# Wait until this succeeds before importing.
docker exec pg-recovery pg_isready -h 127.0.0.1 -U "$PG_RESTORE_ADMIN" -d postgres
docker exec -i pg-recovery psql -X -v ON_ERROR_STOP=1 \
  -U "$PG_RESTORE_ADMIN" -d postgres < "$PG_DUMPS/globals.sql"
for dump in "$PG_DUMPS"/*.dump; do
  args=()
  [[ "$(basename "$dump")" == postgres.dump ]] || args+=(--create)
  docker exec -i pg-recovery pg_restore --exit-on-error \
    -U "$PG_RESTORE_ADMIN" -d postgres "${args[@]}" < "$dump" || break
done
```

Check each database and its application credentials. For a replacement host,
reassign bootstrap-owned objects to their intended owners and remove the
temporary administrator. Confirm host authentication uses SCRAM, then stop
and remove only `pg-recovery` before starting the normal `postgres` service
against the recovered directory. No volume pruning is needed.

For one application database, keep the shared cluster and its other databases.
Stop that application's writers, restore into a new database first, verify it,
and choose the cutover separately; this full-cluster recipe is for empty storage.
