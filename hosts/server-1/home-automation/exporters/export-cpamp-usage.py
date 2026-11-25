#!/usr/bin/env python3
"""Daily point-in-time snapshots of CPAMP usage at API list prices, for Home Assistant.

Reads CPA Manager Plus's usage.sqlite read-only and prices every successful
request at the per-token API price in CPAMP's current price book (the
"API-equivalent cost": what the request would cost billed per token, ignoring
subscriptions). Pricing rules come from scripts/effective-cost.py.

Each closed local day is written once to <archive>/days/YYYY/YYYY-MM-DD.json and
never rewritten, so later price changes leave past days untouched. The price
book a day was priced with is kept as <archive>/prices/<sha16>.json. Days still
open are priced live: today, yesterday until a 6-hour grace period passes, and
for up to 14 days any day with requests to a model missing from the price book
(so a price added in CPAMP still counts).

The Home Assistant feed (--output) holds all-time cumulative counters (frozen
days + open days) and trailing 7-day rates. Use state_class: total sensors on
the counters; HA statistics then give per-day/week/month changes.

Usage:
  export-cpamp-usage.py --db PATH --archive DIR --output FILE [--tz ZONE]
  export-cpamp-usage.py --print-ha-yaml command_line|template
"""

import argparse
import datetime as dt
import importlib.util
import json
import os
import pathlib
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "effective_cost", pathlib.Path(__file__).resolve().parents[4] / "scripts" / "effective-cost.py")
ec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ec)

SCHEMA = 1
GRACE = dt.timedelta(hours=6)            # late-arriving events window before a day is frozen
STALL_LIMIT = dt.timedelta(days=3)       # freeze anyway if no newer events exist by then
UNPRICED_HOLD = dt.timedelta(days=14)    # keep days with unpriced models open this long for a price to be added
BACKFILL_AFTER = dt.timedelta(hours=48)  # frozen this late => priced with a later price book
ROLLING_DAYS = 7
HA_ENTITY = "sensor.cpamp_usage_updated"

# Coarse model families for Home Assistant sensors; first match wins, rest are "other".
# Day files keep exact model names, so changing this only regroups (never reprices).
FAMILIES = [
    ("claude_opus", "Claude Opus", r"claude-opus"),
    ("claude_other", "Claude Sonnet/Haiku", r"claude-"),
    ("gpt", "GPT", r"(^|/)gpt-|codex"),
    ("kimi", "Kimi", r"kimi"),
    ("deepseek", "DeepSeek", r"deepseek"),
    ("glm", "GLM", r"glm"),
    ("qwen", "Qwen", r"qwen"),
    ("grok", "Grok", r"grok"),
    ("other", "Other models", r""),
]

COUNTERS = ("requests", "failed_requests", "input_tokens", "uncached_input_tokens", "cache_read_tokens",
            "cache_write_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens",
            "cache_write_ttl_unknown_tokens", "output_tokens", "reasoning_tokens", "unpriced_requests", "unpriced_tokens")
MONEY = ("api_equivalent_usd", "cache_savings_usd")

EVENT_SQL = """
select timestamp_ms, requested_model, resolved_model, failed, auth_provider_snapshot, api_key_hash,
       session_id, input_tokens, cached_tokens, cache_tokens, cache_read_tokens, cache_creation_tokens,
       normalized_total_input_tokens, normalized_cache_read_tokens, normalized_cache_creation_tokens,
       output_tokens, reasoning_tokens, latency_ms, ttft_ms,
       json_extract(raw_json, '$.tokens.cache_creation_1h_tokens') as cache_write_1h_tokens,
       json_extract(raw_json, '$.tokens.cache_creation_5m_tokens') as cache_write_5m_tokens
from usage_events where timestamp_ms >= ? and timestamp_ms < ?
"""


def family_of(model):
    for key, _, pattern in FAMILIES:
        if re.search(pattern, model or "", re.IGNORECASE):
            return key
    return "other"


def new_bucket():
    return {**{k: 0 for k in COUNTERS}, **{k: 0.0 for k in MONEY}}


def add_bucket(into, other):
    for k in COUNTERS + MONEY:
        into[k] += other.get(k, 0)


def finish_bucket(b):
    for k in MONEY:
        b[k] = round(b[k], 6)
    b["cache_hit_rate"] = round(b["cache_read_tokens"] / b["input_tokens"], 4) if b["input_tokens"] else None
    return b


def day_bounds(day, tz):
    start = dt.datetime.combine(day, dt.time(), tz)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tz)
    return start, end


def ms(t):
    return int(t.timestamp() * 1000)


def key_aliases(db):
    try:
        return {r["api_key_hash"]: r["alias"] for r in db.execute("select api_key_hash, alias from api_key_aliases")}
    except sqlite3.OperationalError:
        return {}


def summarize_day(db, day, tz, prices, tiers, book_hash, aliases, now):
    start, end = day_bounds(day, tz)
    totals, models, providers, clients = new_bucket(), {}, {}, {}
    latency, ttft, sessions = {}, {}, set()
    for row in db.execute(EVENT_SQL, (ms(start), ms(end))):
        row = dict(row)
        parts = ec.event_parts(row, prices, tiers)
        model = parts["model"] or "unknown"
        key_hash = row.get("api_key_hash") or ""
        client = f"key:{key_hash[:8]}" if key_hash else "unknown"
        if client not in clients:
            clients[client] = {"alias": aliases.get(key_hash), **new_bucket()}
        targets = [totals, models.setdefault(model, new_bucket()),
                   providers.setdefault(row.get("auth_provider_snapshot") or "unknown", new_bucket()), clients[client]]
        if row["failed"]:
            for b in targets:
                b["failed_requests"] += 1
            continue
        if row.get("session_id"):
            sessions.add(row["session_id"])
        if row.get("latency_ms"):
            latency.setdefault(model, []).append(row["latency_ms"])
        if row.get("ttft_ms"):
            ttft.setdefault(model, []).append(row["ttft_ms"])
        tokens = parts["input"] + parts["output"]
        for b in targets:
            b["requests"] += 1
            b["input_tokens"] += parts["input"]
            b["uncached_input_tokens"] += parts["fresh"]
            b["cache_read_tokens"] += parts["cache_read"] + parts["cache_compat"]
            b["cache_write_tokens"] += parts["cache_write"]
            b["cache_write_5m_tokens"] += parts["cache_write_5m"]
            b["cache_write_1h_tokens"] += parts["cache_write_1h"]
            b["cache_write_ttl_unknown_tokens"] += parts["cache_write_ttl_unknown"]
            b["output_tokens"] += parts["output"]
            b["reasoning_tokens"] += row.get("reasoning_tokens") or 0
            if parts["cost"] is None:
                b["unpriced_requests"] += 1
                b["unpriced_tokens"] += tokens
            else:
                b["api_equivalent_usd"] += parts["cost"]
                b["cache_savings_usd"] += parts["cache_savings"]
    for model, b in models.items():
        b["median_latency_ms"] = round(statistics.median(latency[model])) if model in latency else None
        b["median_ttft_ms"] = round(statistics.median(ttft[model])) if model in ttft else None
    by_cost = lambda d: {k: finish_bucket(b) for k, b in sorted(d.items(), key=lambda kv: -kv[1]["api_equivalent_usd"])}
    return {
        "schema": SCHEMA,
        "date": day.isoformat(),
        "timezone": tz.key,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "priced_at": now.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "backfilled": now - end > BACKFILL_AFTER,
        "price_book_sha16": book_hash,
        "pricing": "API list price per token from the CPAMP price book; subscriptions ignored",
        "totals": {**finish_bucket(totals), "sessions": len(sessions)},
        "by_model": by_cost(models),
        "by_provider": by_cost(providers),
        "by_client": by_cost(clients),
    }


def write_once(path, data):
    """Create path atomically; never replace an existing file. Returns True if written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        os.unlink(tmp)


def write_replace(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


def day_path(archive, day):
    return archive / "days" / f"{day.year:04d}" / f"{day.isoformat()}.json"


def save_price_book(archive, prices, tiers, book_hash):
    strip = lambda d: {k: v for k, v in d.items() if k not in ("raw_json", "source_model_id")}
    write_once(archive / "prices" / f"{book_hash}.json", {
        "price_book_sha16": book_hash,
        "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "prices": {m: strip(p) for m, p in sorted(prices.items())},
        "context_tiers": {m: [strip(t) for t in ts] for m, ts in sorted(tiers.items())},
    })


def ha_feed(records, frozen_dates, today, now):
    """Cumulative counters and trailing rates from all day records (sorted by date)."""
    totals, families = new_bucket(), {key: new_bucket() for key, _, _ in FAMILIES}
    window_start = (today - dt.timedelta(days=ROLLING_DAYS)).isoformat()
    roll_total, roll_families = new_bucket(), {key: new_bucket() for key, _, _ in FAMILIES}
    for rec in records:
        add_bucket(totals, rec["totals"])
        in_window = window_start <= rec["date"] < today.isoformat()
        if in_window:
            add_bucket(roll_total, rec["totals"])
        for model, b in rec["by_model"].items():
            fam = family_of(model)
            add_bucket(families[fam], b)
            if in_window:
                add_bucket(roll_families[fam], b)
    rate = lambda b, num, den: round(100 * b[num] / b[den], 2) if b[den] else None
    cumulative = lambda b: {k: (round(b[k], 6) if k in MONEY else b[k]) for k in COUNTERS + MONEY}
    rolling = lambda b: {
        "cache_hit_pct": rate(b, "cache_read_tokens", "input_tokens"),
        "api_equivalent_usd": round(b["api_equivalent_usd"], 4),
        "requests": b["requests"],
        "failure_pct": round(100 * b["failed_requests"] / (b["requests"] + b["failed_requests"]), 2)
        if b["requests"] + b["failed_requests"] else None,
        "unpriced_token_pct": round(100 * b["unpriced_tokens"] / (b["input_tokens"] + b["output_tokens"]), 2)
        if b["input_tokens"] + b["output_tokens"] else None,
    }
    last = next((r for r in reversed(records) if r["date"] < today.isoformat()), None)
    return {
        "sampled_at": int(now.timestamp()),
        "today": today.isoformat(),
        "last_frozen_day": max(frozen_dates) if frozen_dates else None,
        "frozen_days": len(frozen_dates),
        "first_day": records[0]["date"] if records else None,
        "totals": cumulative(totals),
        "families": {k: cumulative(b) for k, b in families.items()},
        "rolling_7d": {"totals": rolling(roll_total), **{k: rolling(b) for k, b in roll_families.items()}},
        "yesterday": None if last is None else {
            "date": last["date"],
            "api_equivalent_usd": last["totals"]["api_equivalent_usd"],
            "top_models": [
                {"model": m, "api_usd": round(b["api_equivalent_usd"], 2), "input": b["input_tokens"],
                 "output": b["output_tokens"], "cache_hit_pct": rate(b, "cache_read_tokens", "input_tokens")}
                for m, b in list(last["by_model"].items())[:8]],
        },
    }


def run(db_path, archive, output, tz, now=None):
    now = now or dt.datetime.now(tz)
    today = now.astimezone(tz).date()
    db = ec.open_db(db_path)
    try:
        prices, tiers, book_hash = ec.load_prices(db)
        first_ms, latest_ms = db.execute("select min(timestamp_ms), max(timestamp_ms) from usage_events").fetchone()
        aliases = key_aliases(db)
        existing = {p.stem for p in (archive / "days").glob("*/*.json")}
        candidates = [dt.date.fromisoformat(d) for d in existing] + [today]
        if first_ms:
            candidates.append(dt.datetime.fromtimestamp(first_ms / 1000, tz).date())
        first_day = min(candidates)
        records, frozen, written = [], [], 0
        day = first_day
        while day <= today:
            path = day_path(archive, day)
            if day.isoformat() in existing:
                records.append(json.loads(path.read_text()))
                frozen.append(day.isoformat())
            else:
                rec = summarize_day(db, day, tz, prices, tiers, book_hash, aliases, now)
                _, end = day_bounds(day, tz)
                settled = latest_ms is not None and latest_ms >= ms(end) or now - end >= STALL_LIMIT
                priced = rec["totals"]["unpriced_tokens"] == 0 or now - end >= UNPRICED_HOLD
                if now - end >= GRACE and settled and priced:
                    if not written:
                        save_price_book(archive, prices, tiers, book_hash)
                    if not write_once(path, rec):  # lost a race: keep whatever is on disk
                        rec = json.loads(path.read_text())
                    frozen.append(day.isoformat())
                    written += 1
                records.append(rec)
            day += dt.timedelta(days=1)
    finally:
        db.close()
    feed = ha_feed(records, frozen, today, now)
    feed["price_book_sha16"] = book_hash
    write_replace(output, feed)
    return written


def local_zone(name):
    name = name or os.environ.get("TZ") or ""
    if not name:
        link = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in link:
            name = link.split("/zoneinfo/", 1)[1]
    if not name:
        sys.exit("cannot determine the local time zone; pass --tz")
    return ZoneInfo(name)


# ---- Home Assistant configuration (printed, then merged into the include files by hand) ----

def _attr(path):
    expr = f"state_attr('{HA_ENTITY}', '{path[0]}')"
    for p in path[1:]:
        expr = f"({expr} or {{}}).get('{p}')"
    return expr


def ha_yaml(kind):
    if kind == "command_line":
        return """# Merge this list item into the existing command_line include.
- sensor:
    name: CPAMP Usage Updated
    unique_id: cpamp_usage_updated
    device_class: timestamp
    icon: mdi:update
    command: "cat /config/cpamp_usage.json"
    scan_interval: 300
    value_template: "{{ value_json.sampled_at | timestamp_custom('%Y-%m-%dT%H:%M:%S%z', false) }}"
    json_attributes:
      - today
      - first_day
      - last_frozen_day
      - frozen_days
      - price_book_sha16
      - totals
      - families
      - rolling_7d
      - yesterday
"""
    fresh = f"as_timestamp(states('{HA_ENTITY}'), 0) > as_timestamp(now()) - 7200"
    lines = ["# Merge these items into template.sensor, alongside any existing sensor entries.", "sensor:"]

    def sensor(uid, name, icon, path, unit, state_class, scale=None, device_class=None):
        value = _attr(path)
        state = f"{{{{ (({value}) / {scale}) | round(3) }}}}" if scale else f"{{{{ {value} }}}}"
        lines.extend([
            f"- name: {name}",
            f"  unique_id: {uid}",
            f"  icon: {icon}",
            f"  unit_of_measurement: '{unit}'",
            f"  state_class: {state_class}",
            *([f"  device_class: {device_class}"] if device_class else []),
            f"  availability: \"{{{{ {fresh} and ({value}) is number }}}}\"",
            f"  state: \"{state}\"",
        ])

    groups = [("total", "AI", ("totals",), ("rolling_7d", "totals"))] + [
        (key, f"AI {label}", ("families", key), ("rolling_7d", key)) for key, label, _ in FAMILIES]
    for key, label, base, roll in groups:
        sensor(f"cpamp_{key}_api_cost", f"{label} API-Equivalent Cost", "mdi:cash", (*base, "api_equivalent_usd"),
               "USD", "total", device_class="monetary")
        sensor(f"cpamp_{key}_input_tokens", f"{label} Input Tokens", "mdi:import", (*base, "input_tokens"),
               "M tokens", "total", scale=1000000)
        sensor(f"cpamp_{key}_cache_read_tokens", f"{label} Cache Read Tokens", "mdi:cached",
               (*base, "cache_read_tokens"), "M tokens", "total", scale=1000000)
        sensor(f"cpamp_{key}_output_tokens", f"{label} Output Tokens", "mdi:export", (*base, "output_tokens"),
               "M tokens", "total", scale=1000000)
        sensor(f"cpamp_{key}_cache_hit_7d", f"{label} Cache Hit Rate 7d", "mdi:percent", (*roll, "cache_hit_pct"),
               "%", "measurement")
    sensor("cpamp_total_cache_write_tokens", "AI Cache Write Tokens", "mdi:database-plus",
           ("totals", "cache_write_tokens"), "M tokens", "total", scale=1000000)
    sensor("cpamp_total_cache_write_5m_tokens", "AI Cache Write 5m Tokens", "mdi:timer-sand",
           ("totals", "cache_write_5m_tokens"), "M tokens", "total", scale=1000000)
    sensor("cpamp_total_cache_write_1h_tokens", "AI Cache Write 1h Tokens", "mdi:timer-outline",
           ("totals", "cache_write_1h_tokens"), "M tokens", "total", scale=1000000)
    sensor("cpamp_total_reasoning_tokens", "AI Reasoning Tokens", "mdi:brain", ("totals", "reasoning_tokens"),
           "M tokens", "total", scale=1000000)
    sensor("cpamp_total_cache_savings", "AI Cache Savings", "mdi:piggy-bank", ("totals", "cache_savings_usd"),
           "USD", "total", device_class="monetary")
    sensor("cpamp_total_requests", "AI Requests", "mdi:counter", ("totals", "requests"), "requests", "total")
    sensor("cpamp_total_failed_requests", "AI Failed Requests", "mdi:alert-circle", ("totals", "failed_requests"),
           "requests", "total")
    sensor("cpamp_total_failure_rate_7d", "AI Failure Rate 7d", "mdi:percent", ("rolling_7d", "totals", "failure_pct"),
           "%", "measurement")
    sensor("cpamp_total_unpriced_share_7d", "AI Unpriced Token Share 7d", "mdi:help-circle",
           ("rolling_7d", "totals", "unpriced_token_pct"), "%", "measurement")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("CPAMP_USAGE_DB"))
    ap.add_argument("--archive", default=os.environ.get("CPAMP_USAGE_ARCHIVE"))
    ap.add_argument("--output", default=os.environ.get("CPAMP_USAGE_OUTPUT"))
    ap.add_argument("--tz", default=os.environ.get("CPAMP_USAGE_TZ"))
    ap.add_argument("--print-ha-yaml", choices=("command_line", "template"))
    args = ap.parse_args()
    if args.print_ha_yaml:
        sys.stdout.write(ha_yaml(args.print_ha_yaml))
        return
    if not (args.db and args.archive and args.output):
        sys.exit("--db, --archive and --output (or CPAMP_USAGE_* variables) are required")
    started = time.monotonic()
    written = run(args.db, pathlib.Path(args.archive), pathlib.Path(args.output), local_zone(args.tz))
    print(f"froze {written} day(s) in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
