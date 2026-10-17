# condenseit

CondenseIt digest reader (`NEWS_DOMAIN`). The image is published to GHCR from
its own repository and set as `CONDENSEIT_IMAGE` in `.env`.

- **Requires:** ai-gateway (client key in `/opt/condenseit/secrets`).
- **Network:** its own `condenseit` bridge for feed fetching, plus
  `cpa_policy_model`.
- **Data:** `/opt/condenseit` (`config.yaml`, `data/`, `secrets/`).
- **Backup:** the directory, with an SQLite online copy of `condenseit.db`.

## Telemetry

The image includes telemetry dependencies; export is enabled through runtime
settings. Pin the published image by digest in the private
`/opt/condenseit/compose.env`. Application instrumentation is maintained in the
application fork, on an upstream-compatible `feat/optional-otel` branch; image
publishing integration lives on `deploy/condenseit-otel`. Neither requires
upstream to adopt fleet-specific routing, credentials or container settings.

### Runtime configuration

Copy `telemetry.env.example` to `/opt/condenseit/telemetry.env` with mode 0600.
Set the existing authenticated HTTPS OTLP base endpoint and stable source host,
then enable telemetry. Keep the existing model gateway URL, model and key.

Create a dedicated writer with `scripts/otel-writer.sh add condenseit`, privately
capturing its one-time output in `/opt/condenseit/secrets/otel-writer` (0600).
Do not recreate an existing writer as a retry. Compose mounts that file as a
secret; the entrypoint derives the SDK's encoded Basic-auth header in memory.
The client receives no Langfuse project keys or Grafana/read credentials.

The dedicated Docker bridge must be allowed to reach the host's existing HTTPS
listener. UFW treats container-to-host traffic as INPUT, even when the target
is the host's tailnet address. With deployment-local values for the dedicated
subnet and ingress IPv4 address, the scoped rule is:

```sh
sudo ufw allow in proto tcp from "$CONDENSEIT_SUBNET" to "$OTEL_INGRESS_IPV4" port 443 comment condenseit-otel
```

Use the fixed subnet rather than an ephemeral container IP or bridge name.
This permits HTTPS to the selected host address, including other Caddy virtual
hosts: UFW cannot restrict HTTP paths or hostnames. Keep the existing trusted
TLS, writer authentication, path restrictions and loopback-only backend ports.
The container does not need host networking, an IPv6 route, a shared collector
network or direct access to Alloy's unprotected receiver.

Recreate only Condenseit after updating its environment/image:

```sh
docker compose --project-directory hosts/server-1 --file hosts/server-1/compose.yaml up -d --no-deps condenseit
python3 hosts/server-1/backup/infra-backup-declared.py check --live
```

The existing backup declaration includes the whole runtime directory, covering
the telemetry environment and writer file. No new backup job is needed.

### Identity and filtering

Every digest has a unique `condenseit-<uuid>` session. The application attaches
these fields on every span **before** the collector forwards it to Langfuse:

| Langfuse field | Value |
| --- | --- |
| Top-level metadata `application` | `condenseit` |
| Top-level metadata `workflow` | `digest` |
| Top-level metadata `execution_mode` | `scheduled`, `web`, `cli`, or `manual` |
| Trace tag | `application:condenseit` |
| Trace tag | `workflow:digest` |
| Trace tag | `execution_mode:<mode>` |
| Session | Unique run ID |
| Environment | `production` for this service |
| User | Source host, added by the existing collector |

In Langfuse Traces/Observations select `application:condenseit`, or metadata
`application = condenseit`, and environment `production`. Open/group the session
to see its pipeline, parallel articles, model generations, save and post-run
operations. Add `execution_mode:scheduled` for daily jobs. The app does not
pretend to be a coding harness; no `harness` inference is added to Alloy.
Only new runs are instrumented; old digests are not replayed.

Grafana provisions a **Condenseit** dashboard, with correlated lifecycle events,
per-call token totals, latency and the last successful digest. Production-only
queries exclude acceptance fixtures. Ordinary container logs still use
`{container="condenseit"}`; OTel events use
`{service_name="condenseit"} | deployment_environment_name="production"`.
Session and trace IDs remain record fields, never indexed stream labels or
metric dimensions.

Prometheus receives run counts, duration, model-call latency, known token counts
and the last-success timestamp. The dashboard totals token events once, including
cached input already contained in input usage; it does not add cache/reasoning
subsets again or infer costs. Other providers, embeddings and reranking currently
have enclosing workflow spans but no detailed model instrumentation.

### Health, content and loss boundary

The existing 07:00 scheduler remains the owner of daily runs. A successful
non-dry execution advances `condenseit_digest_last_success_seconds`.
`CondenseitDigestStale` evaluates a 27-hour allowance plus 30 minutes pending;
its 48-hour historical maximum tolerates the application's zero-valued gauge
after a restart. `CondenseitTelemetryMissing` detects 30 minutes without a
freshness sample, pending for another 15 minutes. This detects missed starts
independently of an execution's own failure event. The existing Grafana bridge
can display these alerts; notification delivery still requires a configured
contact point and is not claimed by ingestion verification.

This deployment explicitly enables bounded content capture (16,000 characters
per attribute) for model prompts/responses and root arguments/result summary.
Collector secret redaction runs before Langfuse mapping/storage. Credentials,
raw headers and raw exception text are excluded by the app; collector redaction
is not a complete private-information filter. The upstream default is capture off.

SDK exporters use bounded memory queues and timeouts, flush after each run and
shut down with the process. There is no client disk spool or history replay;
unreachable collectors, full queues and abrupt process termination can lose
records. Collector queues, store retention and backups are separate boundaries.
An HTTP 200 proves ingress acceptance; validate actual stored records too.

To disable, set `CONDENSEIT_OTEL_ENABLED=false` in the private runtime file and
recreate only Condenseit. Its image and credential mount may remain. If removing
the integration entirely, delete only its scoped UFW rule and dedicated writer;
do not revoke another machine's shared writer. Restore an earlier image by
changing only `CONDENSEIT_IMAGE`, preserving unrelated current settings.

The application branch includes `docs/telemetry.md`. See
[fleet agent telemetry](../../../docs/agent-telemetry.md) for the existing
collector/storage contract.
