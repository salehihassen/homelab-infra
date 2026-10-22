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

### Grafana memory headroom

Grafana has a 1 GiB container limit and `GOMEMLIMIT=256MiB`. The latter is a
Go soft memory target inherited by each datasource subprocess, not a cap on
their combined memory or on all RSS. Keep substantial space between that
target and the container limit for plugins, mapped files and non-Go memory.

On 2026-10-16, the former 512 MiB limit caused confirmed memory-cgroup OOM
kills during repeated Agents dashboard loads. An isolated seven-day replay
returned modest query responses (largest about 1.8 MB), then a subsequent
browser load killed Grafana before any panel queries were sent. A Go target
reduced pressure in the isolated comparison; the larger container budget
provides additional headroom. This is a bounded mitigation, not proof that
memory cannot grow under future workloads. Verify repeated browser loads,
query errors, container restarts and cgroup `memory.events` after changes.
Do not assume a later recovered `State.OOMKilled=false` means no earlier OOM:
also inspect kernel logs and Docker events. A brief reverse-proxy 502 during
an OOM restart is an effect, not necessarily a routing fault.

After applying the mitigation, seven-day browser checks passed for all hosts
and concurrent loads of the local and remote host filters: 168 datasource
requests, no query failures, and no restarts or cgroup OOM events. The sampled
container working-set peak during the concurrent check was 549 MiB, above
the former limit and below the new 1 GiB budget. Recheck under normal use;
this short replay does not establish a long-term memory-growth baseline.

## Control-plane monitoring

Grafana provisions **Telemetry health** from
`grafana/provisioning/dashboards/telemetry-health.json`. Prometheus scrapes
Alloy's own metrics every 15 seconds, independently of Alloy's export pipeline.
Alloy's embedded exporters send private remote-write samples for seven
dependency probes and six filesystem mounts:

- Langfuse web and worker, ClickHouse, MinIO, Loki and Grafana HTTP readiness;
  Redis TCP availability only (not an authenticated Redis command).
- Loki, Prometheus, ClickHouse, MinIO, Redis and Alloy queue filesystems.
  Alloy receives only narrow read-only data-directory mounts for the first
  five; it does not mount the whole host filesystem for this check.

`prometheus-rules.yml` evaluates collector/dependency outages, sustained
backlogs, queue capacity, export/enqueue failures, receiver refusals, low disk
space, filesystem read errors and missing probe/disk metrics. Most outage
rules wait one minute; missing metrics wait two minutes and low disk space
waits five. Export/refusal counters look back five minutes, so those alerts
can remain active briefly after recovery. Queue size is exporter batches,
not a count of spans or bytes.

`grafana/provisioning/alerting/telemetry.yaml` bridges firing Prometheus alerts
into Grafana every 20 seconds, preserving their original name in the `signal`
label. Configure a contact point and notification policy in Grafana before
expecting messages; no destination or credentials are provisioned here.
Never commit contact-point secrets. This monitors a running control plane,
not total host loss: use a separate host for independent availability checks.

After changes, validate with `bash scripts/validate-stack.sh prometheus` and
`bash scripts/validate-stack.sh alloy`; the script selects its isolated CI
Compose automatically when the host's version differs.
Rule regression fixtures in `prometheus-rules.test.yml` cover healthy, failing,
missing-metric and recovered states, including a trace-only client with no
application metrics and a failing trace exporter. The Condenseit-specific
dashboard and freshness rules are retired; its upstream instrumentation sends
traces to Langfuse, while ordinary Docker logs remain in Loki. See the
[trace-only rollout and test guide](../../../docs/condenseit-trace-only.md).
`bash scripts/validate-stack.sh grafana` tests dashboard retirement against an
isolated instance of the pinned Grafana image, retaining the other dashboards
and the provider's `disableDeletion: true` policy. Apply only the affected containers;
Grafana alert provisioning changes require its restart. A scoped recovery
drill must restore the stopped service in a finally/trap handler, verify
queued IDs downstream, queue drain, and alert firing/resolution. Do not fill
production disks or stop databases to simulate failures.

## Security note

Alloy mounts Docker's socket read-only so it can discover containers, labels, and
their log streams. A read-only mount still exposes powerful Docker API metadata;
only trusted images/configuration should receive it. If Alloy were used solely as
an OTLP receiver for applications that actively push logs, it would not need the
socket. Here it is needed specifically for automatic collection of existing
Docker stdout/stderr logs.
