# quotes

Quotes web app (`QUOTE_DOMAIN`; `QUOTES_DOMAIN` redirects to it). Image built
from its own repository and published to GHCR (`QUOTES_IMAGE`).

- **Requires:** postgres (database `ductape`, settings in
  `/opt/postgresql/quotes.env`).
- **Backup:** `pg_dump` of `ductape` plus the env file.
