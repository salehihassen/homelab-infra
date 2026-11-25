#!/usr/bin/env bash
set +x
set -euo pipefail

# Both files contain shell assignments. Never echo their contents.
set -a
source /opt/homeassistant/secrets/fal-usage-export.env >/dev/null 2>&1
set +a
source "${FAL_USAGE_SECRETS_FILE:?Set FAL_USAGE_SECRETS_FILE}" >/dev/null 2>&1
set +x
export FAL_BILLING_API_KEY
exec /usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/export-fal-usage.py"
