#!/usr/bin/env bash
# Fail if any tracked (or staged) file contains a private identifier: real
# hostnames, domains, usernames, paths, bucket names. The patterns are
# extended regular expressions, one per line, in the untracked
# local/private-patterns.txt, so the list itself never enters Git.
#
#   bash scripts/check-private.sh           # tracked and untracked, unignored files
#   bash scripts/check-private.sh --staged  # the index (pre-commit hook)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
patterns=local/private-patterns.txt
if [[ ! -r "${patterns}" ]]; then
  echo "check-private: no ${patterns}; skipping (see README.md)" >&2
  exit 0
fi
active="$(grep -vE '^\s*(#|$)' "${patterns}" || true)"
[[ -n "${active}" ]] || { echo "check-private: ${patterns} has no patterns" >&2; exit 1; }

matches=0
check() {  # name, content on stdin
  local name="$1" hits
  # Paths are checked too: a file name can identify the host.
  if hits="$( { printf '%s\n' "${name}"; cat; } | grep -niEf <(printf '%s\n' "${active}") )"; then
    printf '%s\n' "${hits}" | sed "s|^|${name}:|" >&2
    matches=1
  fi
}
if [[ "${1:-}" == "--staged" ]]; then
  while IFS= read -r -d '' file; do
    git show ":${file}" | check "${file}"
  done < <(git diff --cached --name-only --diff-filter=ACMR -z)
else
  while IFS= read -r -d '' file; do
    [[ -f "${file}" && ! -L "${file}" ]] && check "${file}" < "${file}"
  done < <(git ls-files -z --cached --others --exclude-standard)
fi
if ((matches)); then
  echo "check-private: private identifiers found above" >&2
  exit 1
fi
echo "No private identifiers in tracked files."
