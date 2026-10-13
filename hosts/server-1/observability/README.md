# Docker observability stack

Grafana, Loki, Alloy and Prometheus, included in the host's Compose project like
every other stack. Besides Docker logs, Alloy is the intake for agent telemetry
(OTLP from coding agents and scripts on tailnet machines); see
[`docs/agent-telemetry.md`](../../../docs/agent-telemetry.md). It forwards
traces to the `langfuse` stack, which this stack therefore requires. Configuration is tracked here (`loki-config.yaml`, `alloy-config.alloy`,
`grafana/provisioning/`); state stays in `/opt/observability/data`, and
`HOST_LABEL` comes from `/opt/observability/.env` (symlinked as `.env` here).
Since 2026-10-05 these containers carry `compose_project="opt"`, not
`observability`, in Loki labels.

## Local endpoints

- Grafana: `127.0.0.1:13000`
- Loki: `127.0.0.1:3100`
- Alloy UI: `127.0.0.1:12345`
- Alloy OTLP/HTTP receiver: `127.0.0.1:4318` (agent telemetry)
- Prometheus: no host port; Grafana and Alloy reach `prometheus:9090` on the stack network

The ports are loopback-only. Caddy already exposes Grafana through the configured
`GRAFANA_DOMAIN`, with Grafana's login, and Loki through `LOKI_DOMAIN` on the
host's Tailscale addresses. Loki ingress requires Basic authentication and allows
only `/loki/api/v1/push` and `/ready`; other paths return 403. Add remote writers
to the existing authenticated handler, preserving its path restriction. Never
replace it with an unrestricted Loki proxy or publish port 3100 on another
interface. Reads use Grafana over the private Docker network.

## Agent telemetry ingress

`OTEL_DOMAIN` is a write-only door like `LOKI_DOMAIN`. It allows only
`/v1/traces`, `/v1/logs` and `/v1/metrics`; other paths return 403. Each machine
has its own Basic-auth account, stored as `<name> <bcrypt hash>` in an untracked
file under `/opt/observability/otel-writers/` (mounted into Caddy). Manage the
accounts with `scripts/otel-writer.sh add|remove|list`, which reloads Caddy.
`scripts/check-routing.py` enforces the path and auth boundary. `OTEL_DOMAIN`
lives in the same `routing.env` as the Grafana and Loki domains. If it is unset,
Caddy skips this whole `site.caddy`, including Grafana and Loki.

Alloy's OTLP pipeline, in `alloy-config.alloy` after the Docker pipeline, runs
these steps:
1. Redacts common secret formats.
2. Maps Claude Code and Codex span attributes to Langfuse's input/output, model
   and usage fields, and drops Codex's internal spans.
3. Batches and sends through a persistent on-disk queue
   (`otelcol.storage.file`, which needs `--stability.level=public-preview`):
   - logs to Loki's native OTLP endpoint;
   - metrics to Prometheus's OTLP receiver, which converts delta temporality
     to cumulative;
   - traces to Langfuse, authenticated with the project keys in
     `/opt/observability/secrets/langfuse_{public,secret}_key`.

Grafana provisions the **Agents** dashboard from
`grafana/provisioning/dashboards/agents.json`. UI edits are not saved, so change
the JSON instead. Loki accepts lines and structured metadata up to 1 MB for full agent content
and indexes `host.name` as a label. Prometheus keeps 90 days and promotes
`service.name`, `host.name` and the deployment environment to labels.

## Retention and disk behavior

Loki keeps 14 days of logs. This is a time retention rule, not a hard byte cap.
The current host mounts a dedicated 20 GiB ext4 logical volume at the Loki data
directory. Its size is the physical ceiling; monitor free space independently
of retention. Recheck the actual mount with `findmnt` before changing placement.

Each container in this project also rotates its own Docker JSON logs at three
10 MiB files. Existing containers require recreation after adding equivalent
Compose `logging` settings; doing so does not delete named volumes or bind-mounted
application data.

Prometheus data (`/opt/observability/data/prometheus`, owned by uid 1000) is
on the root filesystem and is not backed up.

## Security note

Alloy mounts Docker's socket read-only so it can discover containers, labels, and
their log streams. A read-only mount still exposes powerful Docker API metadata;
only trusted images/configuration should receive it. If Alloy were used solely as
an OTLP receiver for applications that actively push logs, it would not need the
socket. Here it is needed specifically for automatic collection of existing
Docker stdout/stderr logs.
