#!/usr/bin/env python3
"""Effective (#2) inference cost report for CPAMP-tracked usage.

Reads CPA Manager Plus's usage.sqlite read-only and a pools config, then reports:
  - #1 API-equivalent cost per pool (tokens x current model price book, CPAMP rules)
  - #2 effective cost: subscription fee allocated across usage by API-equivalent
    weight; metered pools report #1 as #2; free pools report zero.

Point-in-time pricing: each report embeds a hash of the price book used and can
freeze per-pool period snapshots under --out-dir, so later price changes (like a
model getting cheaper) leave frozen past periods untouched. CPAMP dashboards always
show the *current* rate for all history; these frozen files keep point-in-time math.

Usage:
  effective-cost.py --list-accounts --db /path/to/usage.sqlite
  effective-cost.py --db PATH --pools PATH --period YYYY-MM-DD..YYYY-MM-DD [--out-dir DIR]
  effective-cost.py --db PATH --pools PATH --all [--out-dir DIR] [--recompute]
"""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import sys

PER_M = 1_000_000.0


def open_db(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def load_prices(db):
    prices = {}
    for r in db.execute("select * from model_prices"):
        prices[r["model"]] = dict(r)
    tiers = {}
    cols = [c[1] for c in db.execute("pragma table_info(model_price_context_tiers)")]
    for r in db.execute("select * from model_price_context_tiers"):
        tiers.setdefault(r["model"], []).append(dict(r))
    for model, rows in tiers.items():
        rows.sort(key=lambda x: x["threshold_tokens"])
    book_hash = hashlib.sha256(
        json.dumps(
            {m: {k: v for k, v in p.items() if not k.endswith("_ms") and k not in ("raw_json", "source_model_id")}
                 for m, p in prices.items()},
            sort_keys=True, default=str).encode()).hexdigest()[:16]
    return prices, tiers, book_hash


def tier_price(base, tiers, total_in):
    price = base
    for t in tiers:
        if total_in > t["threshold_tokens"]:
            price = t
    return price


def v(row, key):
    return row.get(key) or 0


def token_parts(row):
    """Split one event's tokens into the segments CPAMP prices separately."""
    total_in = v(row, "normalized_total_input_tokens") or v(row, "input_tokens")
    cr = v(row, "normalized_cache_read_tokens") or v(row, "cache_read_tokens")
    cc = v(row, "normalized_cache_creation_tokens") or v(row, "cache_creation_tokens")
    compat = max(max(v(row, "cached_tokens"), v(row, "cache_tokens")) - cr - cc, 0)
    return {
        "input": total_in, "cache_read": cr, "cache_write": cc, "cache_compat": compat,
        "fresh": max(total_in - cr - cc - compat, 0), "output": v(row, "output_tokens"),
    }


def event_parts(row, prices, tiers):
    """Token segments plus API list-price cost and cache savings; cost None if unpriced."""
    model = row["resolved_model"] or row["requested_model"]
    parts = token_parts(row)
    parts.update(model=model, cost=None, cache_savings=None)
    priced_model = model
    price = prices.get(priced_model)
    if price is None:
        priced_model = row["requested_model"]
        price = prices.get(priced_model)
    if price is None or not price.get("prompt_configured"):
        return parts
    price = tier_price(price, tiers.get(priced_model, []), parts["input"])
    prompt = price["prompt_per_1m"]
    compat_rate = price.get("cache_per_1m") or 0
    cr_rate = price["cache_read_per_1m"] if price.get("cache_read_configured") else (
        price.get("cache_per_1m") or prompt * 0.1)
    cc_rate = price["cache_creation_per_1m"] if price.get("cache_creation_configured") else prompt
    parts["cost"] = (parts["fresh"] * prompt + parts["cache_compat"] * compat_rate
                     + parts["cache_read"] * cr_rate + parts["cache_write"] * cc_rate
                     + parts["output"] * price["completion_per_1m"]) / PER_M
    parts["cache_savings"] = (parts["cache_read"] * (prompt - cr_rate)
                              + parts["cache_compat"] * (prompt - compat_rate)) / PER_M
    return parts


def event_cost(row, prices, tiers):
    """Dollar cost of one event under CPAMP segment rules; None if unpriced."""
    parts = event_parts(row, prices, tiers)
    return parts["cost"], parts["model"]


EVENT_SQL = """
select timestamp_ms, auth_index, auth_provider_snapshot, requested_model, resolved_model, failed,
       input_tokens, cached_tokens, cache_tokens, cache_read_tokens, cache_creation_tokens,
       normalized_total_input_tokens, normalized_cache_read_tokens, normalized_cache_creation_tokens,
       output_tokens
from usage_events where timestamp_ms >= ? and timestamp_ms < ?
"""


def load_events(db, start_ms, end_ms):
    return [dict(r) for r in db.execute(EVENT_SQL, (start_ms, end_ms))]


def matches(row, match):
    """Match on exact column value, or model prefix for requested_model_prefix."""
    for key, values in match.items():
        if key == "requested_model_prefix":
            model = row.get("requested_model") or ""
            if any(model.startswith(p) for p in values):
                return True
        elif row.get(key) in values:
            return True
    return False


def tokens_in_out(row):
    total_in = v(row, "normalized_total_input_tokens") or v(row, "input_tokens")
    return total_in, v(row, "output_tokens")


def report_pool(pool, events, prices, tiers, start_ms, end_ms, book_hash, fee, now_ms):
    billing = pool["billing"]
    rows = []
    api_total = eff_total = unpriced_tok = total_tok = 0
    for ev in events:
        if ev["failed"]:
            continue
        cost, model = event_cost(ev, prices, tiers)
        i, o = tokens_in_out(ev)
        total_tok += i + o
        if cost is None:
            unpriced_tok += i + o
            continue
        eff = cost if billing == "metered" else 0.0
        rows.append({"model": model, "api": cost, "effective": eff, "tokens": i + o})
        api_total += cost
    if billing == "subscription" and api_total > 0 and fee is not None:
        for r in rows:
            r["effective"] = fee * r["api"] / api_total
    eff_total = sum(r["effective"] for r in rows)
    per_model = {}
    for r in rows:
        m = per_model.setdefault(r["model"], {"calls": 0, "tokens": 0, "api": 0.0, "effective": 0.0})
        m["calls"] += 1
        m["tokens"] += r["tokens"]
        m["api"] += r["api"]
        m["effective"] += r["effective"]
    provisional = end_ms > now_ms
    return {
        "pool": pool["id"], "billing": billing, "fee": fee, "fee_configured": not (pool['billing'] == 'subscription' and fee is None),
        "status": "provisional" if provisional else "closed",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "price_book_sha16": book_hash,
        "api_equivalent_usd": round(api_total, 6),
        "effective_usd": round(eff_total, 6),
        "events": len(rows), "tokens": total_tok,
        "unpriced_token_share": round(unpriced_tok / total_tok, 4) if total_tok else 0.0,
        "effective_usd_per_million_tokens": round(eff_total / (total_tok / PER_M), 6) if total_tok else None,
        "api_value_to_spend": round(api_total / fee, 2) if fee else None,
        "by_model": dict(sorted(per_model.items(), key=lambda kv: -kv[1]["api"])),
    }


def parse_period(text):
    left, right = text.split("..")
    parse = lambda s: int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return parse(left), parse(right)


def md(rep):
    lines = [
        f"### {rep['pool']} — {rep['billing']}, fee {rep['fee']} ({rep['status']})",
        f"api-equivalent ${rep['api_equivalent_usd']:.2f} · "
        f"effective #2: ${rep['effective_usd']:.2f} · "
        f"{rep['events']} calls · {rep['tokens']:,} tokens · unpriced share {rep['unpriced_token_share']:.1%}",
    ]
    if rep['billing'] == 'subscription' and not rep.get('fee_configured'):
        lines.append('subscription fee not configured for this period; showing #1 only')
    if rep.get("effective_usd_per_million_tokens") is not None:
        lines.append(f"effective $/1M tokens: {rep['effective_usd_per_million_tokens']:.5f} · "
                     f"API value ÷ spend: {rep.get('api_value_to_spend')}")
    top = list(rep["by_model"].items())[:8]
    for model, m in top:
        lines.append(f"  {model}: {m['calls']} calls, {m['tokens']:,} tok, api ${m['api']:.3f}, eff ${m['effective']:.3f}")
    return "\n".join(lines)


def list_accounts(db):
    print("auth_index | provider snapshot | label | calls | tokens")
    q = """select auth_index, auth_provider_snapshot, auth_label_snapshot,
           count(*) c, coalesce(sum(total_tokens),0) t from usage_events
           group by 1,2,3 order by t desc limit 60"""
    for r in db.execute(q):
        print(f"{r['auth_index']} | {r['auth_provider_snapshot']} | {r['auth_label_snapshot']} | {r['c']} | {r['t']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db")
    ap.add_argument("--pools")
    ap.add_argument("--period", help="YYYY-MM-DD..YYYY-MM-DD (UTC, end exclusive)")
    ap.add_argument("--all", action="store_true", help="evaluate every period in the pools config")
    ap.add_argument("--out-dir", help="write frozen JSON/MD snapshots here")
    ap.add_argument("--recompute", action="store_true", help="overwrite existing frozen snapshots")
    ap.add_argument("--list-accounts", action="store_true")
    args = ap.parse_args()

    if args.list_accounts:
        if not args.db:
            sys.exit("--db is required")
        list_accounts(open_db(args.db))
        return
    if not (args.db and args.pools):
        sys.exit("--db and --pools are required")

    db = open_db(args.db)
    prices, tier_map, book_hash = load_prices(db)
    config = json.load(open(args.pools))
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    freeze_after_ms = 24 * 3600 * 1000
    out = []
    for pool in config["pools"]:
        periods = pool.get("periods") or []
        if args.period:
            windows = [(args.period, *parse_period(args.period))]
        elif args.all and periods:
            windows = [(f"{p['start']}..{p['end']}", *parse_period(f"{p['start']}..{p['end']}")) for p in periods]
        elif args.all:
            windows = [("last-30d", now_ms - 30 * 24 * 3600 * 1000, now_ms)]
        else:
            sys.exit("pass --period or --all")
        for label, start_ms, end_ms in windows:
            key = f"{pool['id']}/{label.replace('..', '_')}"
            shot = None if not args.out_dir else pathlib.Path(args.out_dir) / (key + '.json')
            frozen_eligible = end_ms < now_ms - freeze_after_ms
            if shot and shot.exists() and frozen_eligible and not args.recompute:
                out.append('(frozen: ' + str(shot) + ')')
                continue
            events = [e for e in load_events(db, start_ms, end_ms)
                      if pool.get("match") and matches(e, pool["match"])]
            fee = next((p.get('fee') for p in periods if p['start'] + '..' + p['end'] == label), None)
            if fee is None:
                fee = next((p['fee'] for p in periods
                            if parse_period(p['start'] + '..' + p['end'])[0] <= start_ms
                            and parse_period(p['start'] + '..' + p['end'])[1] >= end_ms and p.get('fee') is not None), None)
            rep = report_pool(pool, events, prices, tier_map, start_ms, end_ms, book_hash, fee, now_ms)
            rep["label"] = label
            out.append(md(rep))
            if shot:
                shot.parent.mkdir(parents=True, exist_ok=True)
                if frozen_eligible or args.recompute:
                    shot.write_text(json.dumps(rep, indent=1))
                    shot.with_suffix(".md").write_text(md(rep) + "\n")
    print("\n\n".join(out))


if __name__ == "__main__":
    main()
