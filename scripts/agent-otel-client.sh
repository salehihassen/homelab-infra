#!/usr/bin/env bash
# Point this machine's coding agents (Claude Code, Codex, pi) and OTel-aware
# scripts at the homelab OTLP endpoint. Run as the user who runs the agents.
#
#   bash agent-otel-client.sh --endpoint https://otel.<zone> --host-label <name> [--dry-run]
#
# Reads the writer credentials (`name:password`, from scripts/otel-writer.sh on
# the collector host) from stdin, or prompts. Writes only under $HOME:
#   ~/.config/agent-otel/{credentials,env.sh,claude-headers}   (0600/0700)
#   ~/.bashrc and ~/.zshrc (if present): one line sourcing env.sh
#   Claude Code settings.json: telemetry env block + otelHeadersHelper
#   Codex config.toml: an [otel] table (endpoints only, no credentials)
#   pi: installs the pinned pi-otel extension when pi is on PATH
# Each modified file is first copied to <file>.bak-agent-otel. Re-running is
# safe: existing blocks are left alone (Codex) or updated in place (Claude).
# Full content capture is on (prompts, responses, tool input and output);
# the collector redacts common secret formats. See docs/agent-telemetry.md.
set -euo pipefail

endpoint="" host_label="" dry_run=0
while (($#)); do
  case "$1" in
    --endpoint) endpoint="${2%/}"; shift 2 ;;
    --host-label) host_label="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$endpoint" == https://* && -n "$host_label" ]] || {
  echo "Usage: $0 --endpoint https://otel.<zone> --host-label <name> [--dry-run]" >&2; exit 2; }
[[ "$host_label" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "Invalid host label" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/agent-otel"
claude_settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
codex_config="${CODEX_HOME:-$HOME/.codex}/config.toml"
pi_package="npm:@b1tank/pi-otel@0.5.0"

say() { printf '%s\n' "$*"; }
backup() { [[ -e "$1" && ! -e "$1.bak-agent-otel" ]] && cp -p "$1" "$1.bak-agent-otel" || true; }

say "Endpoint:      $endpoint"
say "Host label:    $host_label"
say "Credentials:   $config_dir/credentials"
say "Claude Code:   $claude_settings"
say "Codex:         $codex_config"
say "pi:            $(command -v pi >/dev/null && echo "install $pi_package" || echo 'not found; skipped')"
if ((dry_run)); then say "Dry run: nothing written."; exit 0; fi

if [[ -t 0 ]]; then
  read -rsp "Writer credentials (name:password): " credentials; echo
else
  # A file without a trailing newline still yields its one line.
  read -r credentials || [[ -n "${credentials:-}" ]]
fi
[[ "$credentials" == *:* ]] || { echo "Expected name:password" >&2; exit 2; }

# Check the credentials before changing anything.
status="$(curl -s -o /dev/null -w '%{http_code}' -u "$credentials" \
  -H 'Content-Type: application/json' -d '{}' "$endpoint/v1/logs" || true)"
[[ "$status" == 200 ]] || { echo "Endpoint check failed (HTTP $status); nothing written." >&2; exit 1; }
say "Endpoint accepted the credentials."

umask 077
mkdir -p "$config_dir"
printf '%s' "$credentials" > "$config_dir/credentials"
cat > "$config_dir/env.sh" <<EOF
# Agent telemetry: send OTLP from coding agents and scripts on this machine to
# the homelab collector. Sourced from the shell rc file. The password lives only
# in the 0600 \`credentials\` file (name:password) next to this one.
_agent_otel_dir="\${XDG_CONFIG_HOME:-\$HOME/.config}/agent-otel"
if [ -r "\$_agent_otel_dir/credentials" ]; then
  export OTEL_EXPORTER_OTLP_ENDPOINT="$endpoint"
  export OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"
  export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20\$(base64 < "\$_agent_otel_dir/credentials" | tr -d '\n')"
  export OTEL_RESOURCE_ATTRIBUTES="host.name=$host_label"
  # Full content capture (Claude Code and pi-otel read the same flags).
  export OTEL_LOG_USER_PROMPTS=1 OTEL_LOG_ASSISTANT_RESPONSES=1
  export OTEL_LOG_TOOL_DETAILS=1 OTEL_LOG_TOOL_CONTENT=1
  # pi: enable the pi-otel extension and capture system instructions too.
  export PI_OTEL_ENABLED=true PI_OTEL_CAPTURE_SYSTEM_INSTRUCTIONS=true
  export PI_OTEL_CONTENT_MAX_LENGTH=200000
fi
unset _agent_otel_dir
EOF
cat > "$config_dir/claude-headers" <<'EOF'
#!/bin/sh
# Claude Code otelHeadersHelper: print the OTLP auth header as JSON.
set -eu
credentials="${XDG_CONFIG_HOME:-$HOME/.config}/agent-otel/credentials"
printf '{"Authorization":"Basic %s"}\n' "$(base64 < "$credentials" | tr -d '\n')"
EOF
chmod 600 "$config_dir/credentials" "$config_dir/env.sh"
chmod 700 "$config_dir/claude-headers"
say "Wrote $config_dir"

source_line='[ -r "$HOME/.config/agent-otel/env.sh" ] && . "$HOME/.config/agent-otel/env.sh"'
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  [[ -e "$rc" ]] || continue
  grep -qF 'agent-otel/env.sh' "$rc" && continue
  backup "$rc"
  printf '\n# Agent telemetry (OTLP); see ~/.config/agent-otel/env.sh\n%s\n' "$source_line" >> "$rc"
  say "Sourced env.sh from $rc"
done
[[ -d "$HOME/.config/fish" ]] && say "fish detected: export the env.sh variables in fish yourself (see docs/agent-telemetry.md)."

# Claude Code: settings.json env applies even when Claude is not started from
# a shell; the header comes from the helper, never from this file.
mkdir -p "$(dirname "$claude_settings")"
backup "$claude_settings"
python3 - "$claude_settings" "$endpoint" "$host_label" "$config_dir/claude-headers" <<'PY'
import json, os, sys
path, endpoint, host, helper = sys.argv[1:]
settings = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
settings.setdefault("env", {}).update({
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
    "OTEL_RESOURCE_ATTRIBUTES": f"host.name={host}",
    "OTEL_LOG_USER_PROMPTS": "1",
    "OTEL_LOG_ASSISTANT_RESPONSES": "1",
    "OTEL_LOG_TOOL_DETAILS": "1",
    "OTEL_LOG_TOOL_CONTENT": "1",
})
settings["otelHeadersHelper"] = helper
with open(path, "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")
PY
say "Updated $claude_settings"

# Codex reads the Authorization header from OTEL_EXPORTER_OTLP_HEADERS (env.sh).
if [[ -e "$codex_config" ]] && grep -qE '^\[otel\]' "$codex_config"; then
  say "Codex: $codex_config already has [otel]; left unchanged."
else
  mkdir -p "$(dirname "$codex_config")"
  backup "$codex_config"
  cat >> "$codex_config" <<EOF

# Agent telemetry to the homelab collector. The Authorization header comes from
# OTEL_EXPORTER_OTLP_HEADERS (~/.config/agent-otel/env.sh); no credential here.
[otel]
environment = "homelab"
log_user_prompt = true
log_agent_responses = true
exporter = { otlp-http = { endpoint = "$endpoint/v1/logs", protocol = "binary" } }
trace_exporter = { otlp-http = { endpoint = "$endpoint/v1/traces", protocol = "binary" } }
metrics_exporter = { otlp-http = { endpoint = "$endpoint/v1/metrics", protocol = "binary" } }
EOF
  say "Added [otel] to $codex_config"
fi

if command -v pi >/dev/null; then
  pi_settings="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/settings.json"
  backup "$pi_settings"
  pi install "$pi_package" >/dev/null && say "Installed $pi_package"
fi

say "Done. Open a new shell (or: . $config_dir/env.sh), then run an agent once."
