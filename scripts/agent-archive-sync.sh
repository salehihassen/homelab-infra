#!/usr/bin/env bash
# Copy this machine's agent session histories (Claude Code, Codex, pi) to the
# archive on the collector host, where the nightly backup picks them up. They
# are the canonical record behind Langfuse traces. Append-only: files deleted
# here are kept in the archive (no --delete).
#
#   bash agent-archive-sync.sh --target <user>@<collector> --host-label <name>
#   bash agent-archive-sync.sh ... --install-timer   # also sync hourly (systemd --user)
#
# Destination: <target>:/opt/agent-archive/<host-label>/{claude,codex,pi}/.
# Needs rsync on both ends and SSH access to the target.
set -euo pipefail

target="" host_label="" install_timer=0
while (($#)); do
  case "$1" in
    --target) target="$2"; shift 2 ;;
    --host-label) host_label="$2"; shift 2 ;;
    --install-timer) install_timer=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$target" && "$host_label" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "Usage: $0 --target <user>@<collector> --host-label <name> [--install-timer]" >&2; exit 2; }

archive="/opt/agent-archive/$host_label"
declare -A sources=(
  [claude]="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects"
  [codex]="${CODEX_HOME:-$HOME/.codex}/sessions"
  [pi]="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/sessions"
)

synced=0
for agent in claude codex pi; do
  source_dir="${sources[$agent]}"
  [[ -d "$source_dir" ]] || continue
  rsync -a --mkpath --chmod=Du=rwx,Dgo=,Fu=rw,Fgo= \
    "$source_dir/" "$target:$archive/$agent/"
  echo "synced $source_dir -> $target:$archive/$agent/"
  synced=1
done
((synced)) || echo "No agent session directories found." >&2

if ((install_timer)); then
  unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  script_path="$(readlink -f "${BASH_SOURCE[0]}")"
  mkdir -p "$unit_dir"
  cat > "$unit_dir/agent-archive-sync.service" <<EOF
[Unit]
Description=Copy agent session histories to the homelab archive

[Service]
Type=oneshot
ExecStart=/usr/bin/env bash $script_path --target $target --host-label $host_label
EOF
  cat > "$unit_dir/agent-archive-sync.timer" <<'EOF'
[Unit]
Description=Hourly agent session archive sync

[Timer]
OnCalendar=hourly
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now agent-archive-sync.timer
  echo "Installed agent-archive-sync.timer; it runs while you are logged in"
  echo "(or always, after: sudo loginctl enable-linger $USER)."
fi
