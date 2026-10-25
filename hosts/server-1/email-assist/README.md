# email-assist

Private read-only inbox triage (`EMAIL_ASSIST_DOMAIN`, app-level session login),
published to GHCR by its own repository (`EMAIL_ASSIST_IMAGE`); that repo's
README is the user guide. The login name is `EMAIL_ASSIST_WEB_USERNAME` in
`.env`.

- **Requires:** postgres (database and role `email_assist`), ai-gateway, and
  protonmail-bridge. The app container has no host networking: the
  `email-assist-imap-socket` relay (host network) exposes Bridge IMAP as a Unix
  socket in `/opt/email-assist/run`, mounted read-only into the app.
- **Mail providers:** `proton` (Personal, Bridge) and `zoho` (Business, Zoho
  Mail US over IMAPS `imappro.zoho.com:993`, reached directly through
  `postgres_network`'s outbound access) are defined in `compose.yaml`; their
  usernames come from `.env` (see `.env.example`). Enable IMAP in Zoho; use
  the full email address and an app-specific password, stored mode 600 in
  `/opt/email-assist/secrets/zoho-imap-password` (or entered in the UI).
  Keep credentials in `secrets/`, not `.env`, so they stay out of the
  container environment.
  More providers can be added on the app's **Providers** page, or as
  `MAIL_<NAME>_*` lines in `compose.yaml` (the app repo's `.env.example`
  documents them); fields set there are locked in the UI.
- **Data:** `/opt/email-assist/{secrets,state}`; mail state lives in Postgres.
- **Backup:** `pg_dump` of `email_assist` plus secrets and state.
