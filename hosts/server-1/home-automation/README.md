# home-automation

Home Assistant (`HOME_DOMAIN`, host network) and Z-Wave JS UI (`ZWAVE_DOMAIN`)
with the USB stick from `ZWAVE_DEVICE`.

- Home Assistant reaches Z-Wave JS at `ws://localhost:3000`; both Z-Wave ports
  are published on loopback only.
- `exporters/`: systemd **user** services that write AI-usage and Proton Mail
  count samples into `/opt/homeassistant/config/*.json` for Home Assistant
  sensors. The units in `~/.config/systemd/user/*-export.service` run these
  files directly from this repository. Path examples are in
  `exporters/usage-export.env.example`. Claude exporter tests:
  `python3 -m unittest discover -s hosts/server-1/home-automation/exporters -p 'test_export_claude_usage.py'`.
- **Data:** `/opt/homeassistant/{config,secrets,other_assets}`, `/opt/z-wave-js`.
- **Backup:** those paths, with SQLite online copies of the recorder and Zigbee
  databases.

Image tags are pinned; Renovate proposes Home Assistant upgrades.

## fal billing exporter

See [fal setup](fal-usage.md) for the 15-minute credit and model-spending
exporter and Home Assistant entities.

## CondenseIt headlines

The [CondenseIt integration runbook](condenseit-digest.md) describes the
five-minute local API exporter, compact `sensor.condenseit_top_news` entity,
and a built-in Markdown card to paste into a UI-managed dashboard. Credentials
stay in host files; dashboard YAML and storage records stay untouched.

## Antigravity quotas

[Antigravity quotas](antigravity/README.md) use one Home Assistant command-line
poll of the official host CLI every 15 minutes, with eight template entities
for the four remaining percentages and their reset timestamps. It uses a
restricted localhost SSH key and needs no exporter service or systemd timer.

## Claude usage exporter

`export-claude-usage.py` writes the Claude quota sample used by Home
Assistant. Configure `CLAUDE_USAGE_CREDENTIALS_SOURCE`,
`CLAUDE_USAGE_CREDENTIALS_FILE`, `CLAUDE_USAGE_OUTPUT`, and
`CLAUDE_USAGE_STATE_FILE` in a mode-0600, untracked environment file loaded with
`EnvironmentFile=` in the systemd user service. Keep the state file in a
private host directory; if omitted, it defaults to `.usage-export-state.json`
beside the credential file. State and lock files use mode `0600`.

The `claude-usage-export.timer` uses `OnUnitInactiveSec=10min`, and the exporter
also enforces a ten-minute minimum interval in persisted state and locks against
overlapping runs. On rate limits or transient failures, cooldowns double from
10 minutes to a three-hour cap, with up to 60 seconds of positive jitter. A
longer `Retry-After` always takes priority. Cooldowns survive restarts and token
changes.

Use `CLAUDE_USAGE_CREDENTIALS_SOURCE=cpa` and point the credentials file at one
active CPA Claude account's JSON file. The exporter reads `access_token` and the
timezone-qualified `expired` timestamp, and rejects disabled or non-Claude
credentials. CPA alone owns renewal and credential writes. The exporter waits
for CPA to rotate expired tokens, and re-reads the file on each permitted poll
and once after a 401 in case rotation overlapped the request. It never uses
the refresh token or starts Claude Code in CPA mode. No credentials enter the
quota JSON, retry state, or logs. Real credential paths belong only in local
configuration, never in this repository.

The optional `claude_code` source (also the default for older configurations)
reads a separate Claude Code login. In that mode, set `CLAUDE_USAGE_EXECUTABLE`
to Claude Code's absolute path to attempt legacy renewal of expired
tokens: after the cooldown, the exporter runs `claude auth status` once (45 s
timeout, no output kept). Claude Code owns the OAuth refresh and credential
writes; the credential file must be named `.credentials.json`. Failed renewals
use the same backoff, persisted before the CLI starts. A 401 blocks further
requests with that token until it changes. Failures keep the last good sample
and its timestamp; Home Assistant marks it unavailable after 30 minutes.

## Synthetic usage exporter

`export-synthetic-usage.sh` loads the private API key and runs the weekly-only
exporter. The [quota API](https://dev.synthetic.new/docs/synthetic/quotas) is
`GET https://api.synthetic.new/v2/quotas` with Bearer authentication. Its public
example still shows an older `subscription` counter; the current account
response exposes `planWeeklyLimit.percentRemaining`, `nextRegenAt`, and
`nextRegenPercent`. Percentages are percentage points, including values below
1%. The next regeneration is a partial refill, not a full weekly reset.

The exporter writes `weekly_remaining`, `weekly_next_refill_at` (Unix seconds),
`weekly_next_refill_percent`, `weekly_next_refill_remaining`, and `sampled_at`.
`weekly_next_refill_effective_percent` caps the next increment at the missing
quota, so a nearly full bar shows only the amount it can actually recover.
Synthetic [publishes a fixed refill cadence](https://synthetic.new/rate-limits)
of 2 percentage points every 202 minutes (about 14.3 points/day). The exporter
uses the API's next tick and increment, plus that documented interval, to
estimate `weekly_refills_until_full` and `weekly_full_refill_at`, assuming no
further usage. It exports `weekly_refill_interval_seconds` as well. These are
discrete ticks: full recovery occurs at the next tick plus the intervals needed
for the remaining increments. The full timestamp is null when already full,
when the increment is zero, or while awaiting a fresh next-tick timestamp.
The full estimate uses the unrounded percentage; the visible balance is rounded.
The host's `sensor.synthetic_weekly_refill_in` shows the next effective
increment, countdown and resulting balance. `sensor.synthetic_weekly_full_in`
shows the no-further-usage countdown, and
`sensor.synthetic_weekly_full_estimate_at` provides the estimated timestamp.
When full, both countdowns read `Already full` and the full timestamp is
unavailable. Dashboard configuration remains unchanged.

It ignores the legacy
`subscription` counter and separate search quota. No five-hour quota or credit
balances are exported. The existing weekly Home Assistant entity IDs stay the
same; their attributes use the next refill time and percentage. Remove the
retired five-hour remaining and refill helper entities from their owning
`command_line.yaml` and `template.yaml` includes, without editing dashboards.

`synthetic-usage-export.timer` polls every three minutes. Failed requests or
invalid quota values preserve the previous sample and its timestamp. Keep
`SYNTHETIC_USAGE_SECRETS_FILE` and `SYNTHETIC_USAGE_OUTPUT` in local service
configuration; the sourced file must export `SYNTHETIC_API_KEY`. Tests:
`python3 -m unittest discover -s hosts/server-1/home-automation/exporters -p 'test_export_synthetic_usage.py'`.

## Ollama usage exporter

Uses only Ollama's API with an API key; no browser session is involved.
`GET /api/balance` returns the included monthly credit (`allowance_usd`), what
is left of it (`balance_usd`) and the billing period (`period.from`/`until`).
The exporter turns those into `monthly_used`/`monthly_remaining` percentages
and `monthly_resets_at`. `monthly_requests` sums the daily buckets of
`GET /api/usage?range=30d` that overlap the billing period (the first day counts
whole). `POST /api/me` supplies the plan. See
[Ollama's cloud usage API](https://docs.ollama.com/api/cloud-usage); the API
allows 10 requests a minute, and the 5-minute timer makes three.

Keep `OLLAMA_CLOUD_API_KEY` and `OLLAMA_USAGE_OUTPUT` in a mode-0600 local
environment file loaded through the user unit's `EnvironmentFile=`, and run
`export-ollama-usage.py` directly. The exporter never logs or exports the API
key or the account email. An invalid balance or failed request preserves the
previous output and its timestamp; Home Assistant then marks the sample
unavailable after 20 minutes. Missing request counts leave `monthly_requests`
null without blocking the quota. Tests:
`python3 -m unittest discover -s hosts/server-1/home-automation/exporters -p 'test_export_ollama_usage.py'`.
