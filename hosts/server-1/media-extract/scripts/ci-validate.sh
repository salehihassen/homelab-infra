#!/usr/bin/env sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)
# Uses only tracked examples. The root command selects CI Compose automatically.
exec bash "$repo/scripts/validate-stack.sh" compose
