#!/usr/bin/env bash
# Install the backup failure alerts (OnFailure= notifier + hourly overdue check).
#
#   sudo bash hosts/<host>/backup/alerts/install.sh --user NAME --helper PATH [--dry-run] [--test-message]
#   sudo bash hosts/<host>/backup/alerts/install.sh --uninstall
#
# --user    account whose Ping Me configuration (~/.config/personal-ping-me)
#           delivers the alerts; the notifier runs as that account.
# --helper  that account's Ping Me scripts/ping_me.py.
# --test-message  afterwards, send one "[TEST] ... ignore" message through the
#           installed systemd path.
#
# Never starts, stops or edits the backup jobs' own unit files: it adds one
# drop-in per job (OnFailure= plus a success-time ExecStopPost) and reloads
# systemd. Timers keep their schedule across a daemon-reload.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
[[ ${EUID} -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

user="" helper="" dry_run=false uninstall=false test_message=false
while (($#)); do
  case "$1" in
    --user) user="${2:?--user needs a value}"; shift 2 ;;
    --helper) helper="${2:?--helper needs a value}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --uninstall) uninstall=true; shift ;;
    --test-message) test_message=true; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

jobs=(infra-backup.service infra-backup-maintenance.service)
etc=/etc/systemd/system
script=/usr/local/sbin/infra-backup-alert
env_file=/etc/infra-backup/alert.env
status_dir=/var/lib/infra-backup-status
units=(infra-backup-alert@.service infra-backup-alert-check.service infra-backup-alert-check.timer)

run() { if "${dry_run}"; then echo "would run: $*"; else "$@"; fi; }

if "${uninstall}"; then
  for job in "${jobs[@]}"; do run rm -f "${etc}/${job}.d/50-failure-alert.conf"; done
  run systemctl disable --now infra-backup-alert-check.timer || true
  for unit in "${units[@]}"; do
    run rm -f "${etc}/${unit}"
    run rm -rf "${etc}/${unit}.d"
  done
  run rm -f "${script}"
  run systemctl daemon-reload
  for job in "${jobs[@]}"; do
    "${dry_run}" || echo "${job}: $(systemctl show "${job}" -p OnFailure)"
  done
  echo "Removed. Kept ${env_file}, ${status_dir} and /var/lib/infra-backup-alert; delete them by hand if wanted."
  exit 0
fi

[[ -n "${user}" && -n "${helper}" ]] || { echo "Usage: --user NAME --helper PATH (or --uninstall)" >&2; exit 2; }
id -u "${user}" >/dev/null 2>&1 || { echo "No such user: ${user}" >&2; exit 1; }
[[ "${user}" != root ]] || { echo "Use the account that owns the Ping Me configuration, not root." >&2; exit 1; }
[[ "${helper}" = /* && "${helper}" != *[[:space:]]* ]] || { echo "--helper must be an absolute path without spaces." >&2; exit 1; }
group="$(id -gn "${user}")"
# Validates the credentials locally as that user; contacts nothing.
sudo -u "${user}" -H python3 "${helper}" config-check >/dev/null \
  || { echo "Ping Me config-check failed for ${user}; fix that first." >&2; exit 1; }

install_text() {  # target mode content
  local target="$1" mode="$2" content="$3"
  if [[ -e "${target}" ]] && [[ "$(cat "${target}")" == "${content}" ]]; then
    echo "unchanged ${target}"; return 0
  fi
  echo "update    ${target}"
  [[ -e "${target}" ]] && { diff -u "${target}" <(printf '%s\n' "${content}") || true; }
  if ! "${dry_run}"; then
    [[ -e "${target}" ]] && cp -p "${target}" "${target}.before-$(date +%Y%m%d)"
    install -d -m 0755 "$(dirname "${target}")"
    printf '%s\n' "${content}" > "${target}.new"
    chmod "${mode}" "${target}.new"; chown root:root "${target}.new"
    mv "${target}.new" "${target}"
  fi
}

install_text "${script}" 0755 "$(cat infra-backup-alert)"
for unit in "${units[@]}"; do
  install_text "${etc}/${unit}" 0644 "$(cat "units/${unit}")"
done
user_dropin="$(printf '[Service]\nUser=%s\nGroup=%s' "${user}" "${group}")"
install_text "${etc}/infra-backup-alert@.service.d/10-user.conf" 0644 "${user_dropin}"
install_text "${etc}/infra-backup-alert-check.service.d/10-user.conf" 0644 "${user_dropin}"
for job in "${jobs[@]}"; do
  install_text "${etc}/${job}.d/50-failure-alert.conf" 0644 "$(cat dropins/50-failure-alert.conf)"
done
install_text "${env_file}" 0600 "$(sed "s|^PING_ME_HELPER=.*|PING_ME_HELPER=${helper}|" alert.env.example)"
run install -d -m 0755 -o root -g root "${status_dir}"

# Seed success markers from systemd's record of this boot, so a reboot before
# the next run cannot produce a false "overdue" alert.
for job in "${jobs[@]}"; do
  [[ -e "${status_dir}/${job}.success" ]] && continue
  result="$(systemctl show "${job}" -p Result --value)"
  exited="$(systemctl show --timestamp=unix "${job}" -p ExecMainExitTimestamp --value)"
  if [[ "${result}" == success && "${exited}" =~ ^@[0-9]+$ ]]; then
    run env SERVICE_RESULT=success EXIT_STATUS=0 ALERT_NOW="${exited#@}" BACKUP_STATUS_DIR="${status_dir}" \
      "${script}" record-result "${job}"
  fi
done

"${dry_run}" && { echo "Dry run: nothing changed."; exit 0; }
systemctl daemon-reload
systemctl enable --now infra-backup-alert-check.timer
# First check: records the monitoring start; alerts only if a job is overdue.
systemctl start infra-backup-alert-check.service || echo "warning: first check failed; see journalctl -u infra-backup-alert-check.service" >&2

for job in "${jobs[@]}"; do
  echo "${job}: $(systemctl show "${job}" -p OnFailure)"
done
systemctl list-timers infra-backup-alert-check.timer --no-pager | head -2
if "${test_message}"; then
  systemd-run --quiet --wait --pipe --collect -p User="${user}" -p Group="${group}" \
    -p EnvironmentFile="${env_file}" "${script}" test-message
fi
echo "Installed. Rollback: sudo bash $(pwd)/install.sh --uninstall"
