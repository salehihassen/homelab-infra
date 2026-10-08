#!/usr/bin/env bash
# Check or recreate the links between a host's stacks and its runtime env files.
#
#   bash scripts/env-files.sh check [host]  # every runtime file exists and is linked
#   bash scripts/env-files.sh link  [host]  # create missing repo symlinks (new clone)
#
# The mapping is hosts/<host>/env-files.tsv. Runtime files stay on the host and
# in its restic backups, never in Git. On a new host, restore them from a
# snapshot or create them from the matching *.env.example template.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
command="${1:-check}"
host="${2:-server-1}"
mapping="hosts/${host}/env-files.tsv"
[[ -r "${mapping}" ]] || { echo "Missing ${mapping}" >&2; exit 1; }

status=0
while IFS=$'\t' read -r link live; do
  [[ -z "${link}" || "${link}" == \#* ]] && continue
  [[ -e "${live}" ]] || { echo "missing       ${live}"; status=1; }
  [[ "${link}" == - ]] && continue
  link="hosts/${host}/${link}"
  case "${command}" in
    check)
      if [[ "$(readlink "${link}" 2>/dev/null)" == "${live}" ]]; then
        echo "linked        ${link}"
      else
        echo "NOT LINKED    ${link} -> ${live}; run link"; status=1
      fi
      ;;
    link)
      if [[ -L "${link}" || ! -e "${link}" ]]; then
        ln -sfn "${live}" "${link}"
        echo "linked        ${link}"
      else
        echo "REFUSED       ${link} is a regular file; move it aside first"; status=1
      fi
      ;;
    *) echo "Usage: bash scripts/env-files.sh check|link [host]" >&2; exit 2 ;;
  esac
done <"${mapping}"
exit "${status}"
