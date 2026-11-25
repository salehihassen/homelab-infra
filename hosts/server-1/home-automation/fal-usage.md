# fal credit and spending

Matches the Antigravity pattern: one HA command sensor polls every 15 minutes
through restricted localhost SSH; three template sensors expose credit and
estimated model spending. Credentials stay on the host. No timer or JSON cache.

1. Install `exporters/export-fal-usage.{py,sh}` in `/opt/homeassistant/bin/`.
   Configure `exporters/fal-usage.env.example` at
   `/opt/homeassistant/secrets/fal-usage-export.env` (0600) with the existing
   `FAL_BILLING_API_KEY` secrets-file path and IANA timezone. Use a `BILLING` key.
2. Give HA a dedicated SSH key at `/config/.ssh/fal_usage` and pinned host key
   at `/config/.ssh/fal_known_hosts`. Restrict authorization to loopback and
   force the wrapper command, with a nonblocking lock and 110-second timeout.
3. Merge [fal-sensors.yaml](fal-sensors.yaml) into `command_line.yaml`, replacing
   the SSH username. Merge [fal-template.yaml](fal-template.yaml) into
   `template.sensor`. Validate HA configuration and reload both integrations.

Entities keep the IDs `sensor.fal_credit_remaining`, `sensor.fal_spend_today`,
`sensor.fal_spend_month_to_date`, and `sensor.fal_usage_updated`. Spending uses
local calendar boundaries and excludes separate serverless/compute charges.
Failed polls retain the previous sample; monetary sensors expire after 45 minutes.
Dashboards remain editable through the web UI.

Tests: `python3 -m unittest discover -s hosts/server-1/home-automation/exporters
-p 'test_export_fal_usage.py'`.

Rollback: remove these sensor entries and the dedicated SSH authorization/key,
then reload both integrations.
