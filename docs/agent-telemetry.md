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
| Spend and token use over time | The **Agents** dashboard, which sums per-request Loki events (exact even for short-lived agent processes); raw metrics in Prometheus (`claude_code_*`, `codex_*`, `gen_ai_client_*`, `pi_*`) |
| Which machine? | `host_name` label in Loki and Prometheus; Langfuse *User* |

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
the AI gateway); cost comes from Claude Code's own `cost_usd` in Loki and Prometheus.

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
  ClickHouse is cleaned up; watch `df -h /opt/clickhouse/data` (50 GiB volume).
- **Outages:** Alloy queues accepted telemetry on disk
  (`/opt/observability/data/alloy/otel-queue`) and retries, so a Langfuse, Loki
  or Prometheus restart loses nothing. Clients retry briefly, then drop data if
  the collector is unreachable. The session archive is the backstop.
- **Backups:** writer accounts (`/opt/observability/otel-writers`), Langfuse
  settings and its Postgres database, and `/opt/agent-archive` are backed up.
  Loki, Prometheus, ClickHouse and MinIO data are not.
- **Codex trace noise:** Alloy keeps only Codex's turn, model-call and tool
  spans (`otelcol.processor.filter.codex_noise`). Widen its allowlist if you
  need more detail.
