#!/usr/bin/env sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)
# Uses only tracked examples. The root command enforces the CI Compose pin.
exec bash "$repo/scripts/validate-stack.sh" compose
