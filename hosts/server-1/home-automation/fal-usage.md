# fal account credit and spending

The fal exporter polls every 15 minutes through a systemd user timer. It uses
only documented GET endpoints: [account billing](https://fal.ai/docs/platform-apis/v1/account/billing)
with `expand=credits`, and [model usage](https://fal.ai/docs/platform-apis/v1/models/usage)
with `expand=summary`. Use a separate `BILLING` preset key, or a custom policy
with `billing:usage:read`, `auth:keys:read`, and `account:users:read`.
`READONLY` alone supports the balance endpoint but lacks the additional usage
permissions. See [key permissions](https://fal.ai/docs/documentation/model-apis/authentication/key-based#permissions).

The output includes remaining credit in USD, today's estimated model spending,
month-to-date estimated model spending, period boundaries, timezone, and the
last successful sample time. Spending sums `cost_total` after discounts over
all summary pages without filtering models, keys, or users. It uses exact start
and end times (`bound_to_timeframe=false`) in the configured local timezone.
Daily and monthly spending entities expose `last_reset` at their respective
calendar boundaries so long-term statistics handle period changes correctly.
Model usage totals do not represent all possible serverless or compute charges;
the account credit balance remains the source for low-credit monitoring.

Failed requests, malformed amounts, mixed currencies, and incomplete pagination
retain the previous sample and its timestamp. Home Assistant marks samples
unavailable after 45 minutes. The exporter makes no automatic HTTP retries,
does not follow redirects, and never logs response bodies or credential values.
It publishes only whitelisted fields; keys and account identifiers stay out of
the atomic mode-0600 JSON output and entity attributes.

## Install

Install `exporters/export-fal-usage.py` and `exporters/export-fal-usage.sh`
into `/opt/homeassistant/bin/`. Copy `exporters/fal-usage.env.example` to
`/opt/homeassistant/secrets/fal-usage-export.env` with mode 0600. Set:

- `FAL_USAGE_SECRETS_FILE`: existing shell assignments file containing
  `FAL_BILLING_API_KEY`; the wrapper sources it with output suppressed.
- `FAL_USAGE_OUTPUT`: `/opt/homeassistant/config/fal_usage.json`.
- `FAL_USAGE_TIMEZONE`: the desired IANA timezone, such as `UTC`.

Install `exporters/fal-usage-export.service` and `.timer` under the owning
user's `~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user start fal-usage-export.service
systemctl --user enable --now fal-usage-export.timer
```

Append [fal-sensors.yaml](fal-sensors.yaml) to the existing `command_line.yaml`
integration include. This creates:

- `sensor.fal_credit_remaining` (USD)
- `sensor.fal_spend_today` (USD, usage estimate)
- `sensor.fal_spend_month_to_date` (USD, usage estimate)
- `sensor.fal_usage_updated` (timestamp)

The entities read the local sample every minute; external polling remains every
15 minutes. Validate using `docker exec homeassistant python3 -m homeassistant
--script check_config --config /config`, then call `command_line.reload` and
verify all four entities. No dashboard files, dashboard storage records,
automations, or notification settings are modified. Cards remain editable in
the web UI.

Exporter binaries, local settings, and output are covered by the existing
home-automation backup paths. Keep the external credential file covered by its
own existing host backup policy; it is referenced rather than copied. User units
must also remain in the host backup sources. A declaration does not prove a
successful off-host snapshot.

Tests: `python3 -m unittest discover -s hosts/server-1/home-automation/exporters
-p 'test_export_fal_usage.py'`.

To roll back, disable the timer, remove only these four integration entries,
validate and reload command-line sensors. Retain the last sample and credentials
until no longer needed.
