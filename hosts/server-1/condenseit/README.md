# condenseit

CondenseIt digest reader (`NEWS_DOMAIN`). The image is published to GHCR from
its own repository and set as `CONDENSEIT_IMAGE` in `.env`.

- **Requires:** ai-gateway (client key in `/opt/condenseit/secrets`).
- **Network:** its own `condenseit` bridge for feed fetching, plus
  `cpa_policy_model`.
- **Data:** `/opt/condenseit` (`config.yaml`, `data/`, `secrets/`).
- **Backup:** the directory, with an SQLite online copy of `condenseit.db`.
