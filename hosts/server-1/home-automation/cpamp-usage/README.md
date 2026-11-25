# CPAMP daily usage snapshots

`exporters/export-cpamp-usage.py` freezes one snapshot per local day of
everything CPA Manager Plus (CPAMP) recorded. It then feeds Home Assistant
cumulative counters, so HA statistics can chart any day, week or month.

## Cost terms

| Term | Meaning | Here |
| --- | --- | --- |
| **API-equivalent cost** (list-price cost) | Tokens × the provider's per-token API price, as if every request were billed per token | Yes: `api_equivalent_usd` |
| **Cache savings** | What cache reads saved compared with paying the full input price, at API prices | Yes: `cache_savings_usd` |
| **Effective (amortized) cost** | Subscription fees spread across the requests they covered; metered usage at cost | No: `scripts/effective-cost.py` |
| **Cash spend** | What was actually paid: subscription fees plus metered bills | No |

The gap between API-equivalent cost and cash spend is the subscription subsidy.

## How snapshots are kept

- **Source:** `usage.sqlite`, opened read-only. Pricing uses CPAMP's price
  book and its segment rules (fresh input, cache read, cache write, output, and
  context tiers), shared with `scripts/effective-cost.py`. Failed requests are
  counted but not priced. Cached prompt tokens are priced as cache reads
  and cache writes at their own rates, never at the full input price.
- **Cache-write TTL:** CPA v8.1.0 and later reports Anthropic's 5-minute and
  1-hour cache writes separately. 1-hour writes are priced at 2× the base input
  price and 5-minute writes at CPAMP's cache-write price (1.25×). Writes with no
  TTL split, such as those from older CPA versions or other providers, use the
  cache-write price and are counted as `cache_write_ttl_unknown_tokens`.
- **Day files:** `<archive>/days/YYYY/YYYY-MM-DD.json`. Each holds totals and
  per-model, per-provider and per-client-key figures:
  - requests, failures and sessions
  - input, cache-read, cache-write (by TTL), output and reasoning tokens
  - cache hit rate
  - API-equivalent cost and cache savings
  - median latency and time to first token

  Client keys are identified by a hash prefix. Their CPAMP alias at freeze time
  is stored as a label only.
- **Write-once:** a file is created atomically and never replaced. A later
  price change in CPAMP doesn't affect closed days. `price_book_sha16` names
  the price book in `<archive>/prices/<sha16>.json` that priced the day.
- **When a day freezes:** six hours after it ends, once CPAMP has recorded a
  later event. If the collector has been quiet, the day freezes three days
  after it ends regardless.
- **Missing prices:** a day with requests to a model missing from the price
  book stays open for up to 14 days, so adding the price in CPAMP still
  counts. Open days are repriced on every run.
- **Backfill:** the first run freezes every past day at the then-current price
  book and marks them `backfilled: true`.
- **Day boundaries:** midnight in the host's time zone. HA statistics weeks
  start on Monday.
- **Corrections:** to correct a day deliberately, move its file aside. The next
  run rebuilds it at current prices.

`/opt/cpa` is already in the nightly backup, so an archive placed under it is
covered.

## Install

1. Copy `exporters/cpamp-usage.env.example` to a private env file (the unit
   reads `/opt/homeassistant/secrets/cpamp-usage-export.env`) and set the
   paths.
2. Link `exporters/cpamp-usage-export.{service,timer}` into
   `~/.config/systemd/user/`, then run:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now cpamp-usage-export.timer
   ```
   The timer runs every 10 minutes. A normal run takes well under a second;
   the first backfill takes a few seconds.
3. Merge `command_line.example.yaml` and `template.example.yaml` into the HA
   includes. They are generated: after changing `FAMILIES` in the exporter,
   regenerate them with `export-cpamp-usage.py --print-ha-yaml
   command_line|template`. Then run `check_config` and reload Command line and
   Template entities.

## Home Assistant entities

- **`sensor.cpamp_usage_updated`:** the last export time. Its attributes hold
  the full feed, including yesterday's top models.
- **Cumulative counters** (`state_class: total`) for all models and for each
  model family:
  - API-equivalent cost
  - input, cache-read and output tokens (in millions)

  The all-models group also counts cache-write tokens (total, 5-minute and
  1-hour), reasoning tokens, cache savings, requests and failed requests. Open days are repriced live, so a
  counter can occasionally dip. `total` handles that; `total_increasing`
  would not.
- **Trailing 7-day rates** (`measurement`):
  - cache hit rate, all models and per family
  - failure rate
  - share of tokens with no price
- **Model families:** `FAMILIES` in the exporter maps exact model names to
  coarse families. Day files keep exact names, so remapping only regroups
  history and never reprices it.

HA only charts from the day the sensors were added. The day files hold the
earlier history.

Dashboards stay UI-managed. Paste cards in the dashboard editor, for example
weekly API-equivalent cost by family:

```yaml
type: statistics-graph
title: Weekly API-equivalent cost
chart_type: bar
period: week
stat_types: [change]
days_to_show: 90
entities:
  - sensor.ai_claude_opus_api_equivalent_cost
  - sensor.ai_gpt_api_equivalent_cost
  - sensor.ai_kimi_api_equivalent_cost
  - sensor.ai_deepseek_api_equivalent_cost
  - sensor.ai_other_models_api_equivalent_cost
```

## Tests

```bash
python3 -m unittest discover -s hosts/server-1/home-automation/exporters -p 'test_export_cpamp_usage.py'
python3 -m unittest scripts/tests/test_effective_cost.py
```
