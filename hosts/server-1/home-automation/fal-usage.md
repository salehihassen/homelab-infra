# fal credit and spending

Polls account credit and estimated model spending every 15 minutes. Uses a
separate `BILLING` key; `READONLY` alone cannot read the usage endpoint.
Spending follows the configured timezone; stale samples become unavailable
after 45 minutes. Model spending excludes separate serverless/compute charges.

1. Install `exporters/export-fal-usage.{py,sh}` in `/opt/homeassistant/bin/`.
2. Copy `exporters/fal-usage.env.example` to
   `/opt/homeassistant/secrets/fal-usage-export.env` (0600). Set the secrets-file
   path containing `FAL_BILLING_API_KEY`, output path
   `/opt/homeassistant/config/fal_usage.json`, and IANA timezone.
3. Install `exporters/fal-usage-export.{service,timer}` in
   `~/.config/systemd/user/`, then run:

   ```sh
   systemctl --user daemon-reload
   systemctl --user start fal-usage-export.service
   systemctl --user enable --now fal-usage-export.timer
   ```

4. Append [fal-sensors.yaml](fal-sensors.yaml) to `command_line.yaml`, validate
   with `docker exec homeassistant python3 -m homeassistant --script check_config
   --config /config`, then call `command_line.reload`.

Entities: `sensor.fal_credit_remaining`, `sensor.fal_spend_today`,
`sensor.fal_spend_month_to_date`, and `sensor.fal_usage_updated`.
Edit dashboard cards through the web UI.

Tests: `python3 -m unittest discover -s hosts/server-1/home-automation/exporters
-p 'test_export_fal_usage.py'`.

Rollback: disable the timer, remove the four sensor entries, and reload.
