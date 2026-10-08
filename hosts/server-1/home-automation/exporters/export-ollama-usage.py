#!/usr/bin/env python3
"""Export Ollama Cloud included-usage percentages for Home Assistant.

Uses only the documented API with an API key (no browser session):
/api/balance for the included monthly credit and its billing period,
/api/usage for request counts, and /api/me for the plan.
"""

import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


BALANCE_URL = "https://ollama.com/api/balance"
# 30 daily buckets always cover a billing period that started within a month.
USAGE_URL = "https://ollama.com/api/usage?range=30d"
ACCOUNT_URL = "https://ollama.com/api/me"


def required_path(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is missing")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


def fetch(url, api_key, method="GET"):
    request = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Ollama request to {url} returned HTTP {exc.code}") from None


def number(value, name):
    # bool is an int subclass; reject it along with strings and NaN/inf.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Ollama returned an invalid {name}")
    return float(value)


def timestamp(value, name):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValueError(f"Ollama returned an invalid {name}") from None
    if moment.tzinfo is None:
        raise ValueError(f"Ollama returned {name} without a timezone")
    return moment


def period_requests(usage, period_start):
    """Requests in daily buckets overlapping the billing period, or None.

    Buckets are whole UTC days, so the first day of the period counts fully.
    Request counts are informational; a format change must not block the quota.
    """
    try:
        return sum(int(bucket["request_count"]) for bucket in usage["buckets"]
                   if timestamp(bucket["until"], "usage bucket end") > period_start)
    except (KeyError, TypeError, ValueError):
        return None


def get_usage():
    api_key = os.environ.get("OLLAMA_CLOUD_API_KEY")
    if not api_key:
        raise ValueError("OLLAMA_CLOUD_API_KEY is missing")

    credit = fetch(BALANCE_URL, api_key)
    included = credit["included"]
    allowance = number(included["allowance_usd"], "included allowance")
    balance = number(included["balance_usd"], "included balance")
    if allowance <= 0:
        raise ValueError("Ollama returned an invalid included allowance")
    period_start = timestamp(included["period"]["from"], "billing period start")
    resets_at = timestamp(included["period"]["until"], "billing period end")
    used = min(100.0, max(0.0, (allowance - balance) / allowance * 100))

    # /api/me also returns the account email; export only the plan.
    plan = fetch(ACCOUNT_URL, api_key, method="POST").get("Plan")
    # Purchased credit is informational and absent on some accounts.
    try:
        purchased = number(credit["purchased"]["balance_usd"], "purchased balance")
    except (KeyError, TypeError, ValueError):
        purchased = None
    return {
        "monthly_used": round(used, 1),
        "monthly_remaining": round(100 - used, 1),
        "monthly_requests": period_requests(fetch(USAGE_URL, api_key), period_start),
        "plan": plan if isinstance(plan, str) else None,
        "monthly_resets_at": int(resets_at.timestamp()),
        "included_balance_usd": balance,
        "included_allowance_usd": allowance,
        "purchased_balance_usd": purchased,
        "sampled_at": int(time.time()),
    }


def write_private_file(output, contents):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(contents)
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    output = required_path("OLLAMA_USAGE_OUTPUT")
    usage = get_usage()
    write_private_file(output, json.dumps(usage, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Ollama usage export failed: {exc}", file=sys.stderr)
        sys.exit(1)
