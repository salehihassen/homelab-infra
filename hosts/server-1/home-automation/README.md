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

## Ollama usage exporter

The Ollama usage endpoint can return either the older `limits.monthly` response
or activity totals with `totals` and `buckets`. Activity totals are not monthly
quota limits: their supported ranges are trailing 24h, 7d, and 30d rather than
the account's billing cycle. With that response, the exporter reads monthly
included credits and the reset timestamp from the authenticated settings page.
See [Ollama's usage and reset rules](https://ollama.com/pricing).

Keep `OLLAMA_CLOUD_API_KEY`, `OLLAMA_USAGE_OUTPUT`, and session-source paths in
a mode-0600 local environment file loaded through the user unit's
`EnvironmentFile=`. Run `export-ollama-usage.py` directly. Configure
`OLLAMA_USAGE_BROWSER_COOKIES_FILE` (a signed-in local Firefox profile's
`cookies.sqlite`) and `OLLAMA_USAGE_COOKIE_FILE` (a private, single-line Cookie
header). Close Firefox once after signing in to import its saved session; its
database can be exclusively locked while running. The browser database is
opened read-only and only unexpired Ollama session cookies are read. After a
successful quota request the exporter saves that session atomically with mode
0600, then uses the saved cookie on subsequent polls, including any session
renewals returned by Ollama. Firefox can then be reopened. If the session expires,
sign in again, close Firefox, and remove the saved cookie file to reimport it.
Either source can also be used alone. Sign-in redirects are rejected without
forwarding session cookies. Free-plan percentage meters and monthly dollar
meters are supported, with the reset scoped to the same usage section.
The exporter never logs or exports API keys, cookies, or account email.

When using the settings page, `monthly_requests` is null because the activity
API doesn't expose an exact billing-cycle request count. The older response
still supplies that count. Invalid pages, expired sessions, and malformed
quotas preserve the previous output and its original timestamp; Home Assistant
then marks the sample unavailable after 20 minutes. If the page omits a reset,
Free accounts use the documented monthly signup anniversary; paid-plan resets
remain unknown. Tests:
`python3 -m unittest discover -s hosts/server-1/home-automation/exporters -p 'test_export_ollama_usage.py'`.
