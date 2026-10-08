# email-assist

Private read-only inbox triage (`EMAIL_ASSIST_DOMAIN`, app-level basic auth),
published to GHCR by its own repository (`EMAIL_ASSIST_IMAGE`); that repo's
README is the user guide. The login name and mailbox address are
`EMAIL_ASSIST_WEB_USERNAME` and `EMAIL_ASSIST_IMAP_USERNAME` in `.env`.

- **Requires:** postgres (database and role `email_assist`), ai-gateway, and
  protonmail-bridge. The app container has no host networking: the
  `email-assist-imap-socket` relay (host network) exposes Bridge IMAP as a Unix
  socket in `/opt/email-assist/run`, mounted read-only into the app.
- **Data:** `/opt/email-assist/{secrets,state}`; mail state lives in Postgres.
- **Backup:** `pg_dump` of `email_assist` plus secrets and state.
