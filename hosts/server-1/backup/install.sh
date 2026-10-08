#!/usr/bin/env bash
# Install the reviewed backup scripts and systemd units from this directory.
# Usage: sudo bash hosts/<host>/backup/install.sh [--dry-run]
# Requires /etc/infra-backup/backup.env (template: backup.env.example).
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
[[ ${EUID} -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
dry_run=false; [[ "${1:-}" == "--dry-run" ]] && dry_run=true
settings=/etc/infra-backup/backup.env
[[ -r "${settings}" ]] || { echo "Create ${settings} from backup.env.example first." >&2; exit 1; }

install_file() {  # source target mode
  local source="$1" target="$2" mode="$3"
  if [[ -e "${target}" ]] && cmp -s "${source}" "${target}"; then
    echo "unchanged ${target}"
    return 1
  fi
  echo "update    ${target}"
  [[ -e "${target}" ]] && diff -u "${target}" "${source}" || true
  if ! "${dry_run}"; then
    [[ -e "${target}" ]] && cp -p "${target}" "${target}.before-$(date +%Y%m%d)"
    install -m "${mode}" -o root -g root "${source}" "${target}"
  fi
}

scripts=(infra-backup infra-backup-maintenance infra-backup-declared.py infra-backup-sqlite.py infra-backup-extra-state.py)
for script in "${scripts[@]}"; do
  for target in "/usr/local/sbin/${script}" "/opt/backup/${script}"; do
    install_file "${script}" "${target}" 0755 || true
  done
done
units_changed=false
for unit in units/*; do
  install_file "${unit}" "/etc/systemd/system/$(basename "${unit}")" 0644 && units_changed=true
done
if ! "${dry_run}"; then
  "${units_changed}" && systemctl daemon-reload
  systemctl enable --now infra-backup.timer infra-backup-maintenance.timer
fi
set -a; . "${settings}"; set +a
python3 ./infra-backup-declared.py --infra "${INFRA_DIR}" --host "${INFRA_HOST}" check --live
