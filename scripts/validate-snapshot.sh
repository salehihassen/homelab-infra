#!/usr/bin/env bash
# Run validate-stack.sh against exactly what is being committed or pushed,
# not the working tree, with the Compose version CI pins.
#
#   bash scripts/validate-snapshot.sh --index [suite]    # staged files (pre-commit)
#   bash scripts/validate-snapshot.sh <commit> [suite]   # a commit (pre-push)
#
# When the host's Compose differs from the pin, a checksum-verified copy of
# the pinned release is cached under ~/.cache and used for this run only.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source_rev="${1:?usage: validate-snapshot.sh --index|<commit> [suite]}"
suite="${2:-all}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir "$work/tree"
if [[ "$source_rev" == --index ]]; then
  git checkout-index --all --prefix="$work/tree/"
else
  git archive "$source_rev" | tar -x -C "$work/tree"
fi
# The private-identifier patterns are untracked; the hooks run that check on
# the repository itself, so the snapshot's own `private` step skips quietly.
bash scripts/with-validation-compose.sh bash "$work/tree/scripts/validate-stack.sh" "$suite" \
  2> >(grep -v '^check-private: no ' >&2)
