#!/usr/bin/env bash
set +x
set -euo pipefail

# This is a shell assignments file, not a systemd EnvironmentFile. Never echo it.
source "${FAL_USAGE_SECRETS_FILE:?Set FAL_USAGE_SECRETS_FILE}" >/dev/null 2>&1
set +x
export FAL_BILLING_API_KEY
exec /usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/export-fal-usage.py"
