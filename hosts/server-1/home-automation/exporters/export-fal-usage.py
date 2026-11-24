#!/usr/bin/env python3
"""Export fal account credit and estimated model spending without account identifiers."""

import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


API_URL = "https://api.fal.ai/v1/"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward credentials to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(path, key, params):
    request = urllib.request.Request(
        API_URL + path + "?" + urllib.parse.urlencode(params),
        headers={"Authorization": "Key " + key, "Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            data = json.load(response, parse_float=Decimal)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"fal {path} returned HTTP {exc.code}") from None
    if not isinstance(data, dict):
        raise ValueError("Invalid fal response")
    return data


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Invalid fal amount")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or not math.isfinite(float(result)):
        raise ValueError("Invalid fal amount")
    return result


def spending(key, start, end, timezone):
    """Sum discounted costs across all models, keys, and paginated summary rows."""
    params = {"expand": "summary", "start": start.isoformat(), "end": end.isoformat(),
              "timezone": timezone, "bound_to_timeframe": "false", "limit": 100}
    total = Decimal(0)
    seen = set()
    for _ in range(25):
        data = fetch("models/usage", key, params)
        rows = data["summary"]
        if not isinstance(rows, list) or not isinstance(data["has_more"], bool):
            raise ValueError("Invalid fal usage page")
        for row in rows:
            if row["currency"] != "USD":
                raise ValueError("Unsupported fal usage currency")
            total += amount(row["cost_total"])
        cursor = data["next_cursor"]
        if not data["has_more"]:
            if cursor is not None:
                raise ValueError("Inconsistent fal usage pagination")
            return float(total)
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ValueError("Invalid fal usage cursor")
        seen.add(cursor)
        params["cursor"] = cursor
    raise ValueError("fal usage pagination exceeded safety limit")


def get_usage(now=None):
    key = os.environ.get("FAL_BILLING_API_KEY")
    if not key:
        raise ValueError("FAL_BILLING_API_KEY is missing")
    timezone = os.environ.get("FAL_USAGE_TIMEZONE", "UTC")
    zone = ZoneInfo(timezone)
    now = datetime.now(zone) if now is None else now.astimezone(zone)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    credits = fetch("account/billing", key, {"expand": "credits"})["credits"]
    if credits["currency"] != "USD":
        raise ValueError("Unsupported fal credit currency")
    balance = float(amount(credits["current_balance"]))
    sample = {
        "credit_remaining_usd": balance,
        "spend_today_usd": spending(key, day_start, now, timezone),
        "spend_month_to_date_usd": spending(key, month_start, now, timezone),
        "currency": "USD",
        "usage_source": "estimate",
        "timezone": timezone,
        "day_start": day_start.isoformat(),
        "month_start": month_start.isoformat(),
        "sampled_at": int(time.time()),
    }
    for period, start in (("daily", day_start), ("monthly", month_start)):
        sample[period + "_attributes"] = {
            "last_reset": start.isoformat(),
            "sampled_at": sample["sampled_at"],
            "timezone": timezone,
            "usage_source": "estimate",
        }
    return sample


def main():
    output = Path(os.environ["FAL_USAGE_OUTPUT"])
    if not output.is_absolute():
        raise ValueError("FAL_USAGE_OUTPUT must be absolute")
    data = get_usage()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Response bodies, exception details, and credentials never enter logs.
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"fal usage export failed: {detail}", file=sys.stderr)
        sys.exit(1)
