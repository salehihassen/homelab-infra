# condenseit

CondenseIt digest reader (`NEWS_DOMAIN`). The image is published to GHCR from
its own repository and set as `CONDENSEIT_IMAGE` in `.env`.

- **Requires:** ai-gateway (client key in `/opt/condenseit/secrets`).
- **Network:** its own `condenseit` bridge for feed fetching, plus
  `cpa_policy_model`.
- **Data:** `/opt/condenseit` (`config.yaml`, `data/`, `secrets/`).
- **Backup:** the directory, with an SQLite online copy of `condenseit.db`.

## Telemetry

Use the combined application fork branch based on current upstream, including
upstream's optional OpenTelemetry instrumentation and the retained application
changes. Pin the published image by digest in the private
`/opt/condenseit/compose.env`; the infrastructure branch does not choose or
upgrade that image. Verify its OCI source/revision labels against the application
checkout before deployment. The older telemetry deployment branch is retired.

Upstream exports **traces only**, through the existing authenticated HTTPS OTLP
endpoint to Alloy and Langfuse. It does not export application metrics or
structured OTel lifecycle logs. Ordinary stdout/stderr collection continues
through Docker discovery in Alloy: use Loki `{container="condenseit"}`.
The shared Agents and Telemetry health dashboards and collector alerts remain.
There is no dedicated Condenseit dashboard or digest-freshness alert.

### Runtime configuration

For a new deployment, copy `telemetry.env.example` to
`/opt/condenseit/telemetry.env` with mode 0600. Set the existing HTTPS OTLP base
endpoint and stable source host, then enable telemetry. Preserve the model
gateway URL, selected model, provider key and existing application settings.
Do not overwrite a deployed environment with an example.

Reuse the existing dedicated writer in `/opt/condenseit/secrets/otel-writer`.
For initial provisioning only, use `scripts/otel-writer.sh add condenseit`,
privately capturing its one-time output in that file with mode 0600. Do not
recreate an existing writer as a retry. Compose mounts the credential as a
secret; the entrypoint derives the SDK's encoded Basic-auth header in memory.
No Langfuse project keys or Grafana/read credentials are passed to the client.

The dedicated Docker bridge must be allowed to reach the host's existing HTTPS
listener. UFW treats container-to-host traffic as INPUT, even when the target
is the host's tailnet address. With deployment-local values, the scoped rule is:

```sh
sudo ufw allow in proto tcp from "$CONDENSEIT_SUBNET" to "$OTEL_INGRESS_IPV4" port 443 comment condenseit-otel
```

This permits HTTPS to the selected host address, including other Caddy virtual
hosts; UFW cannot restrict HTTP paths or hostnames. Keep trusted TLS, writer
authentication, path restrictions and loopback-only backend ports. Host
networking, a shared collector network and direct access to Alloy's unauthenticated
receiver are unnecessary. Reuse the installed rule instead of adding duplicates.

### Identity and filtering

Each digest gets a unique `condenseit-<uuid>` session, with a digest span and
child model-call spans for the OpenAI-compatible summarizer. Upstream supplies:

| Field | Value |
| --- | --- |
| Span attribute `application` and Langfuse application metadata | `condenseit` |
| Span attribute `workflow` | `digest` |
| Trace tag | `application:condenseit` |
| Session | Unique digest run ID |
| Environment | `CONDENSEIT_OTEL_ENVIRONMENT`, default `production` |
| User | Source host, added by the existing collector |

In Langfuse, filter by `application:condenseit` and environment `production`,
then open the session to inspect the digest and model calls. Upstream does not
supply execution-mode metadata or the former workflow/execution-mode tags.
No harness identity is inferred for this application. Old digests are not
replayed; existing historical traces, logs and metrics follow store retention.

Model spans report requested/returned model, duration, known token usage and
error type. Cached input is a subset of total input; do not count it twice.
Other providers, embeddings and reranking have no detailed model instrumentation.
Trace ingestion does not establish model pricing or total spend.

### Health, content and loss boundary

The application's saved scheduler settings remain authoritative. Verify its
status and next run after an upgrade; do not add a second scheduler. Removing
the former freshness metric also removes missed-digest detection. Collector
health alerts cannot prove that a digest ran successfully. Check the application
API/UI and Langfuse until an independent freshness monitor is deliberately added.

Content capture is off by default upstream. This deployment's runtime template
enables bounded model prompt/response capture only with collector redaction in
place. Upstream fixes the limit at 16,000 characters per field; the old
`CONDENSEIT_OTEL_CONTENT_LIMIT` override is unsupported. Raw exception text and
provider credentials are excluded by the application. Collector redaction is
not a complete private-information filter.

The trace exporter uses a bounded in-memory queue and timeouts, flushes after
a digest and shuts down with the process. There is no client disk spool or
history replay. Collector failures, full queues and abrupt exits can lose
spans. HTTP 200 proves ingress acceptance, not downstream storage.

To disable export, set `CONDENSEIT_OTEL_ENABLED=false` in the private runtime
file and recreate only Condenseit. Keep the shared collector/store infrastructure
for other clients. The existing backup declaration covers this runtime directory,
including telemetry settings and writer credentials.

Follow the [trace-only rollout and test guide](../../../docs/condenseit-trace-only.md)
for image verification, retirement of the old live dashboard, acceptance checks
and rollback. The application repository's `docs/telemetry.md` describes the
upstream contract; [fleet agent telemetry](../../../docs/agent-telemetry.md)
describes the shared collector and stores.
