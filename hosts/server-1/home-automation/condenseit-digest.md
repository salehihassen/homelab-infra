# CondenseIt headlines in Home Assistant

The deployed CondenseIt build exposes `GET /api/digests/latest`,
`GET /api/digests` and `GET /api/digests/{id}`. The latest detail contains
`meta.id`, `meta.created_at` and ordered `items`. Use `Authorization: Bearer`
with the effective CondenseIt login password (database setting takes precedence
over its environment secret). The Atom endpoint is `/api/feed/atom`.
This build has no read-only bearer scope; its feed-token guard also accepts that
token across the API. The exporter performs only a GET of the latest digest.

`exporters/export-condenseit-digest.py` calls the loopback API and atomically
writes a mode-0600 compact sample containing the first five items in digest
order. It omits summaries and credentials. An empty digest produces zero items;
failed requests or malformed responses retain the previous sample and timestamp.
The sensor becomes unavailable after 20 minutes without a successful poll.
Digest publication time is separate from poll freshness: an older daily digest
is still valid while the API remains reachable. Links use `/?id=<digest_id>` so
they open the same saved digest even after a new run. There is no item anchor
in this frontend, so each headline opens the containing digest.

## Host setup

Copy `exporters/condenseit-digest.env.example` to
`/opt/homeassistant/secrets/condenseit-digest-export.env` (mode 0600), with local
paths and reader URL. Reference the existing CondenseIt password file; do not
put the password in the sample, command line, repository or entity attributes.
If the password was changed through the application, provision a private file
with that effective password instead. Install the supplied user service and
timer under `~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user start condenseit-digest-export.service
systemctl --user enable --now condenseit-digest-export.timer
```

Add this integration entry to the existing `command_line.yaml` include:

```yaml
- sensor:
    name: CondenseIt Top News
    unique_id: condenseit_top_news
    command: "cat /config/condenseit_digest.json"
    scan_interval: 60
    icon: mdi:newspaper
    value_template: "{{ value_json.item_count }}"
    availability: "{{ (as_timestamp(now()) - value_json.sampled_at) < 1200 }}"
    json_attributes:
      - digest_id
      - created_at
      - items
      - digest_url
      - sampled_at
```

Validate with `docker exec homeassistant python3 -m homeassistant --script
check_config --config /config`. Since command-line sensors are already loaded,
call `command_line.reload`, then verify `sensor.condenseit_top_news`.
See the [Command Line integration documentation](https://www.home-assistant.io/integrations/command_line/).
No Compose, routing or backup declaration changes are required: output and local
environment settings are under existing home-automation backup paths, and the
existing CondenseIt secret is already covered by that stack's backup.

## UI card

Keep the dashboard in storage mode. Add a Manual card through the web UI and
paste the following (adjust the entity ID if Home Assistant assigned a different
one). This is a built-in [Markdown card](https://www.home-assistant.io/dashboards/markdown/);
no dashboard files or custom card dependencies are needed. Titles are escaped
as HTML text so punctuation in headlines cannot alter the generated links.

```yaml
type: markdown
title: Top news
entity_id: sensor.condenseit_top_news
content: |-
  {% set entity = 'sensor.condenseit_top_news' %}
  {% if states(entity) in ['unknown', 'unavailable'] %}
  News is temporarily unavailable.
  {% else %}
  {% set url = state_attr(entity, 'digest_url') %}
  {% for item in state_attr(entity, 'items') or [] %}
  {{ loop.index }}. <a href="{{ url | e }}">{{ item.title | e }}</a>

  {% else %}
  No news items yet.
  {% endfor %}
  {% set created = state_attr(entity, 'created_at') %}
  {% if created %}
  Digest: {{ as_timestamp(created) | timestamp_custom('%b %d, %I:%M %p') }}
  {% endif %}

  <a href="{{ url | e }}">Open CondenseIt</a>
  {% endif %}
```

Exporter tests: `python3 -m unittest discover -s
hosts/server-1/home-automation/exporters -p 'test_export_condenseit_digest.py'`.
