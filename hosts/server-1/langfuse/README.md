# langfuse

Self-hosted [Langfuse](https://langfuse.com) v4: the UI for reading what coding
agents and scheduled agents did (sessions, prompts, responses, tool calls with
arguments and output). It receives only traces. Agents never write here directly:
they push OTLP to the observability stack's `OTEL_DOMAIN`, and Alloy redacts
secrets and forwards traces here. Events and metrics go to Loki and Prometheus.
Client setup is in [`docs/agent-telemetry.md`](../../../docs/agent-telemetry.md).

- **URL:** `LANGFUSE_DOMAIN` (tailnet-only through Caddy; Langfuse login, sign-up
  disabled). The admin user, organisation, `Agents` project and its API key pair
  come from `LANGFUSE_INIT_*` on first start.
- **Requires:** `postgres` (database and role `langfuse` in the shared cluster).
- **Provides:** network `langfuse` (`opt_langfuse`); the observability stack's
  Alloy joins it to reach `http://langfuse-web:3000/api/public/otel`.
- **Services:** `langfuse-web` (loopback `127.0.0.1:13010`), `langfuse-worker`,
  `clickhouse`, `redis`, `minio`. ClickHouse, Redis and MinIO have no host ports
  and run as uid 1000 on their own logical volumes:
  `/opt/clickhouse/data/langfuse`, `/opt/redis/data/langfuse`, `/opt/minio/data/langfuse`.
- **ClickHouse** is tuned for a shared host (`clickhouse/config.xml`: small caches,
  no system log tables, stdout logging). Its only user, `langfuse`, is defined in
  `clickhouse/users.xml` with the password read from the environment.
- **Memory:** limits total about 6.75 GiB (web 2g with a 1.5 GiB Node heap,
  worker 1g, ClickHouse 2g, Redis 256m, MinIO 512m); idle use is much lower.

## Secrets

Service env files under `/opt/langfuse` (mode 0600), never in Git. Variable names
are in `secrets.env.example`:

| File | Used by |
| --- | --- |
| `langfuse.env` | langfuse-web, langfuse-worker (database URL, salt, keys, backing-store passwords, first-start `LANGFUSE_INIT_*`) |
| `clickhouse.env` | clickhouse (`CLICKHOUSE_PASSWORD`) |
| `redis.env` | redis (`REDIS_AUTH`, `REDISCLI_AUTH`) |
| `minio.env` | minio (`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`) |

The project key pair is also in `/opt/observability/secrets/langfuse_public_key`
and `langfuse_secret_key` (no trailing newline) for Alloy. If you rotate the keys
in the Langfuse UI, update those two files and recreate `alloy`.

## Data and backup

- Backed up: the env files and the `langfuse` Postgres database (users, projects,
  keys, settings).
- Not backed up: ClickHouse (traces), MinIO (raw ingestion events) and Redis
  (queue). The canonical record is the agent session archive in
  `/opt/agent-archive` (backed up by `backup/host.yaml`); see
  `docs/agent-telemetry.md`. Traces can be re-sent from it if needed.
- Retention: keep about 90 days of traces. Check ClickHouse volume use in
  Grafana or with `df -h /opt/clickhouse/data`.

## Restore

Restore the env files and the `langfuse` database (see `postgres/README.md`),
create the three data directories owned by uid 1000, then
`docker compose up -d langfuse-web langfuse-worker`. Langfuse runs its
Postgres and ClickHouse migrations on start.
