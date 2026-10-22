# Agent telemetry

Coding agents (Claude Code, Codex, pi) and scheduled agent scripts on any
tailnet machine send OpenTelemetry (OTLP) to the observability stack. You can
see what each agent did: prompts, responses, tool calls with arguments and
output, model, tokens, cost and errors.

```
agents on any tailnet machine ─HTTPS─► Caddy  OTEL_DOMAIN
    one Basic-auth account per machine; only /v1/traces, /v1/logs, /v1/metrics
        └► Alloy (127.0.0.1:4318): redact secrets → map for Langfuse → batch → on-disk queue
              ├ logs    → Loki        every agent event, full content    (14 days)
              ├ metrics → Prometheus  tokens, cost, sessions, durations  (90 days)
              └ traces  → Langfuse    session and tool-call trees        (LANGFUSE_DOMAIN)
agent session files ─► /opt/agent-archive/<machine>/ on the collector host (nightly backup)
```

The configuration lives in `hosts/server-1/observability/` (Alloy pipeline,
Caddy site, Prometheus) and `hosts/server-1/langfuse/`. The Docker log pipeline
in Alloy is separate and unaffected.

## Where to look

| Question | Where |
| --- | --- |
| Overview: cost, tokens, sessions, tool calls, recent prompts and failures | Grafana dashboard **Agents** (filter by machine) |
| What did this session do, step by step? | Langfuse → Sessions or Traces (filter *User* = machine) |
| What exactly did the agent run and what came back? | Langfuse tool observations; Loki `{service_name="claude-code"}` etc. |
| Prompts and replies for Codex / Claude Code | Loki events (`codex.user_prompt`, `codex.agent_response`, `claude_code.user_prompt`, `claude_code.assistant_response`) |
| Spend and token use over time | The **Agents** dashboard sums ingested per-request Loki events without depending on process lifetime (cost currently covers Claude Code only); raw metrics in Prometheus (`claude_code_*`, `codex_*`, `gen_ai_client_*`, `pi_*`) |
| Which machine? | `host_name` label in Loki and Prometheus; Langfuse *User* |

### Filtering by harness

The collector adds top-level Langfuse `harness` metadata and a matching trace
tag on every incoming span, without changing client configuration:

| Original `service.name` | `harness` | Tag |
| --- | --- | --- |
| `claude-code` | `claude-code` | `harness:claude-code` |
| `codex_cli_rs`, `codex_exec` | `codex` | `harness:codex` |
| `pi` | `pi` | `harness:pi` |

In Langfuse, filter **Tags / Trace Tags** by the harness tag and combine it
with **User** to select a machine. Raw service names remain in resource
metadata; Loki labels and Prometheus metrics are unchanged. This applies to
newly ingested spans only, not stored history; resumed sessions may contain
both old and enriched observations.

Explicit `langfuse.trace.metadata.harness` span attributes take precedence,
followed by a span's `harness`, a resource's
`langfuse.trace.metadata.harness`, then a resource's `harness`. Alloy only
infers missing values for the exact service names above. Existing trace tags
are preserved, and the matching harness tag is not appended twice. Unknown
services without an explicit harness pass through without inferred tags.

Condenseit follows upstream's trace-only instrumentation: `application=condenseit`,
`workflow=digest`, and a unique session per digest. Filter Langfuse by tag
`application:condenseit` and environment `production`. There is no dedicated
Condenseit Grafana dashboard or application-metric freshness alert. Ordinary
Docker logs remain available in Loki at `{container="condenseit"}`; these are
separate from OTel log records. See [deployment and filtering](../hosts/server-1/condenseit/README.md)
and the [trace-only rollout and acceptance checks](condenseit-trace-only.md).

Do not infer `application`, `workflow` or `execution_mode` from the harness.
A containerized application can launch a coding harness, or call the model
gateway directly. Application tracing and explicit application identity need
separate instrumentation; gateway access alone does not supply them.

What each agent sends:

| Agent | Langfuse traces | Loki events | Prometheus |
| --- | --- | --- | --- |
| Claude Code | prompt per interaction; model calls with tokens; tool calls with input and output (assistant text is in Loki only) | everything, including assistant responses and cost per request | cost, tokens, sessions, active time, lines changed |
| Codex | turns, model calls, tool calls by name; no content (Codex keeps content out of spans) | everything: prompts, tool arguments and output, responses | API requests, durations, tool calls |
| pi (`pi-otel`) | full GenAI trees: messages, tool arguments and results, tokens | lifecycle events | GenAI token and duration histograms, cost |
| Scripts | whatever they emit; use `gen_ai.*` or `langfuse.*` attributes for rich views | log records | any OTLP metric |

Prometheus series from agents are per process, and Claude Code's are also per
session, so they go stale minutes after an agent exits. Use range functions
(`max_over_time`, `sum_over_time`), or the Loki events the dashboard uses.
Langfuse cannot price models it does not know (for example names prefixed by
the AI gateway); the Agents cost panels instead sum Claude Code's own
`cost_usd` values from Loki `api_request` events within the selected time and
host range. They do not price tokens, resolve proxy aliases, or include other
harnesses. The model field only groups the cost-by-model chart.

Treat this as a **client-reported API-equivalent estimate**, not an invoice or
total gateway spend. Claude Code calculates cost using usage counts and its
model pricing rules (including cache reads/writes and applicable adjustments).
It can identify the model from a response even when the requested name was a
proxy alias; unrecognized models can fall back to other pricing. Alloy does
not correct those estimates. Subscription/OAuth, free or self-hosted routes,
proxy markups, and missing/duplicated telemetry can all differ from billed
spend. A nonzero estimate does not prove a paid API charge.

Codex's current telemetry does not provide a comparable dollar-valued event.
The pinned pi extension exports `pi.gen_ai.cost.usage` as a Prometheus
histogram when pi supplies `usage.cost.total`, but does not put that cost on
its `pi.assistant.message` Loki event. Its metric is not wired into these
Loki-based cost panels. A cross-harness total needs explicit model/route
pricing and provenance, plus accounting for short-lived process counters;
missing or unknown prices must not silently become zero-dollar usage.

## Security model

- Tailnet-only: the Caddy site binds the host's Tailscale addresses. Alloy's
  receiver is published on loopback only. On the collector host, any local
  process can send to `127.0.0.1:4318` without auth.
- Each machine has its own writer account, so you can revoke one without
  touching the others. The site accepts only the three OTLP paths; everything
  else gets 403. Writers cannot read anything: reads go through the Grafana and
  Langfuse logins.
- Full content capture is on. Alloy replaces common secret formats before
  storage: private keys, Anthropic, OpenAI, GitHub and AWS keys, Bearer/Basic
  credentials, `scheme://user:pass@` URLs and `*_TOKEN|SECRET|PASSWORD|KEY=value`
  pairs. Anything else in a prompt or tool output is stored as-is. Add patterns
  to the `redact` processor in `alloy-config.alloy`, and test them with
  `scripts/otel-smoke.py`, which sends fake secrets.
- Credentials never live in Git or in agent config files. Each client keeps
  `name:password` in `~/.config/agent-otel/credentials` (0600). `env.sh` turns it
  into `OTEL_EXPORTER_OTLP_HEADERS`, and a header helper does the same for
  Claude Code.

## Adding a machine

On the collector host, create a writer account. The password is shown once:

```bash
bash scripts/otel-writer.sh add <machine>      # prints <machine>:<password>
bash scripts/otel-writer.sh list
bash scripts/otel-writer.sh remove <machine>   # revoke
```

On the new machine, which must be on the tailnet, run the client setup as the
user who runs the agents. Copy the script over, or run it from a checkout of
this repository:

```bash
bash agent-otel-client.sh --endpoint https://otel.<zone> --host-label <machine> --dry-run
bash agent-otel-client.sh --endpoint https://otel.<zone> --host-label <machine>
# paste <machine>:<password> when prompted
```

It checks the credentials against the endpoint before writing anything, then:

- writes `~/.config/agent-otel/{credentials,env.sh,claude-headers}`;
- sources `env.sh` from `~/.bashrc` / `~/.zshrc`;
- adds the telemetry `env` block and `otelHeadersHelper` to Claude Code's
  `settings.json` (`$CLAUDE_CONFIG_DIR`, default `~/.claude`);
- appends an `[otel]` table to Codex's `config.toml` (`$CODEX_HOME`, default
  `~/.codex`). It applies to every Codex profile, because profiles layer on top
  of `config.toml`;
- installs the pinned `pi-otel` extension if `pi` is on `PATH`.

Every modified file is first copied to `<file>.bak-agent-otel`. To undo, restore
those copies and delete `~/.config/agent-otel`.

For fish, export the variables from `env.sh` in `~/.config/fish/conf.d/`. For
other OTel-aware scripts and services, source `env.sh` (or set the same
variables) and give each a distinct `OTEL_SERVICE_NAME`.

Then archive the machine's session histories to the collector host. This
needs SSH from the machine to the collector:

```bash
bash agent-archive-sync.sh --target <user>@<collector> --host-label <machine>
bash agent-archive-sync.sh --target <user>@<collector> --host-label <machine> --install-timer
```

On the collector host itself, the session directories are backed up in place
through the untracked `backup/host.local.yaml`, so no sync is needed.

### Proxied or wrapped agents

Agents that reach models through the AI gateway or the policy proxy
(`ANTHROPIC_BASE_URL`, Codex `model_provider`) need nothing extra. Telemetry
goes to `OTEL_DOMAIN`, separately from model traffic. Check that:

- wrappers that clear provider variables (`env -u ...`) do not also clear
  `OTEL_*` variables;
- a wrapper that sets `CLAUDE_CONFIG_DIR` or `CODEX_HOME` points at the
  directory the setup script edited (run it once per config directory, with the
  variable exported);
- agents started outside a login shell still work. Claude Code reads its
  settings file either way. Codex and pi need `OTEL_EXPORTER_OTLP_HEADERS` in
  their environment, so set the `env.sh` variables in that launcher.

## Verifying

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.<zone> python3 scripts/otel-smoke.py
```

It prints a session id. You should then see:
- a nested trace in Langfuse, including one failed tool span;
- events in Loki at `{service_name="otel-smoke"}`, with every fake secret replaced by `[REDACTED...]`;
- a counter in Prometheus, `otel_smoke_runs_total`.

Then run each agent once, for example `claude -p "..."`, `codex exec "..." </dev/null`
and `pi -p "..."`, and look for its session in Langfuse.

## Operations

- **Retention:** Loki 14 days, Prometheus 90 days. Langfuse keeps traces until
  cleanup is explicitly configured; the desired 90-day trace policy is not
  enforced. Built-in self-hosted retention requires Enterprise; see
  `hosts/server-1/langfuse/README.md` for the pending choice.
- **Outages:** Alloy retries exports using an on-disk queue
  (`/opt/observability/data/alloy/otel-queue`). Queued trace recovery was tested
  across a short Langfuse ingress outage and graceful Alloy restart. This is
  not a lossless guarantee: pre-queue batches, exhausted queues/retries, abrupt
  crashes, full disks and collector outages have separate loss boundaries.
  Client retry behavior depends on the harness and launcher. Session histories
  help investigate gaps, but no automatic archive-to-trace replay exists.
- **Monitoring:** Grafana's **Telemetry health** dashboard shows dependency
  readiness, exporter queues, disk space and alerts. Prometheus evaluates the
  control-plane rules; Grafana bridges firing alerts. Notification delivery
  needs an operator-configured contact point and policy. An independent host
  must monitor collector-host availability; same-host alerts cannot report
  total host loss. See `hosts/server-1/observability/README.md`.
- **Backups:** writer accounts (`/opt/observability/otel-writers`), Langfuse
  settings and its Postgres database, and `/opt/agent-archive` are backed up.
  Loki, Prometheus, ClickHouse, MinIO, Redis and the Alloy queue are not. Backup
  declaration coverage is not evidence of full-stack recovery; excluded
  telemetry cannot currently be reconstructed from the session archives.
- **Codex trace noise:** Alloy keeps only Codex's turn, model-call and tool
  spans (`otelcol.processor.filter.codex_noise`). Widen its allowlist if you
  need more detail.
