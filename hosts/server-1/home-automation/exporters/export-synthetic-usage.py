#!/usr/bin/env python3
"""Export Synthetic's weekly quota percentage and next regeneration for Home Assistant."""

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


URL = "https://api.synthetic.new/v2/quotas"
# Published cadence: https://synthetic.new/rate-limits (2 percentage points/tick).
REFILL_INTERVAL_SECONDS = 202 * 60


def required_path(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is missing")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Synthetic returned an invalid refill time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Synthetic returned a refill time without a timezone")
    return int(parsed.timestamp())


def get_usage():
    api_key = os.environ.get("SYNTHETIC_API_KEY")
    if not api_key:
        raise ValueError("SYNTHETIC_API_KEY is missing")
    request = urllib.request.Request(URL, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Synthetic quota request returned HTTP {exc.code}") from exc

    weekly = data["planWeeklyLimit"]
    weekly_percent = float(weekly["percentRemaining"])
    refill_percent = float(weekly["nextRegenPercent"])
    if not all(math.isfinite(x) for x in (weekly_percent, refill_percent)):
        raise ValueError("Synthetic returned nonfinite quota values")
    if not all(0 <= x <= 100 for x in (weekly_percent, refill_percent)):
        raise ValueError("Synthetic returned invalid quota values")

    next_refill_at = timestamp(weekly["nextRegenAt"])
    sampled_at = int(time.time())
    refills_until_full = None
    full_refill_at = None
    if weekly_percent == 100:
        refills_until_full = 0
    elif refill_percent > 0 and next_refill_at > sampled_at:
        refills_until_full = math.ceil((100 - weekly_percent) / refill_percent)
        full_refill_at = next_refill_at + (refills_until_full - 1) * REFILL_INTERVAL_SECONDS

    return {
        "weekly_remaining": round(weekly_percent, 1),
        "weekly_next_refill_at": next_refill_at,
        "weekly_next_refill_percent": refill_percent,
        "weekly_next_refill_effective_percent": round(min(100 - weekly_percent, refill_percent), 4),
        "weekly_next_refill_remaining": round(min(100, weekly_percent + refill_percent), 1),
        "weekly_refill_interval_seconds": REFILL_INTERVAL_SECONDS,
        "weekly_refills_until_full": refills_until_full,
        "weekly_full_refill_at": full_refill_at,
        "sampled_at": sampled_at,
    }


def main():
    output = required_path("SYNTHETIC_USAGE_OUTPUT")
    usage = get_usage()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(usage, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Synthetic usage export failed: {exc}", file=sys.stderr)
        sys.exit(1)
