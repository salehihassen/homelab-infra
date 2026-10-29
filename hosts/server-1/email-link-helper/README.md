# email-link-helper

Private read-only sign-in link broker and Chrome/Firefox extension. Source is
the sibling `services/email-link-helper` checkout; Compose and Caddy belong here.
The app has its own bridge network and loopback port 8904. Its independent Unix
socket relay connects to Proton Bridge loopback 1143; no email-assist dependency.

- **URL:** `EMAIL_LINK_HELPER_DOMAIN`, Caddy bound to the host's Tailscale addresses.
- **Authentication:** Argon2 web login and opaque server-side sessions for settings;
  separately revocable device tokens for extension queue reads/claims. Tailnet
  access alone does not authenticate either interface.
- **Runtime:** `/opt/email-link-helper/{app.env,secrets,state,run}`. `compose.env`
  and `routing.env` are linked through `env-files.tsv`. `PUBLIC_ORIGIN` comes from
  routing; `WEB_USERNAME`, `MAIL_PROVIDERS`, `MAIL_<ID>_*`, `LINK_RULES_JSON`,
  `POLL_SECONDS` and `MAX_AGE_SECONDS` may be set in `app.env`. UI settings persist
  encrypted under `state/helper.db`; environment fields take precedence.
- **Secrets:** `encryption-key` (Fernet), `web-password-hash` (Argon2),
  `bridge-cert.pem` (pinned local Bridge TLS certificate), and
  `<provider>-imap-password`. Use the Bridge-generated password, not a Proton
  account password. Keep files mode 600, directories 700, owned by UID 1000.
- **Providers:** Proton Bridge and any verified IMAPS provider (e.g. Zoho).
  Gmail is out of scope. Configure trusted receiving-server `authserv_ids` and
  narrow sender/DKIM/destination allowlists for each rule. Claude rules are
  based on observed archived messages; historical links are not delivered.
- **Settings UI:** provider/rule create, edit and delete forms replace JSON editing.
  Environment-managed fields are locked, passwords are preserved when left blank,
  and stale-tab revisions prevent overwriting newer settings. Remove a provider's
  rules before deleting it. Extension v0.2.0 shows unsaved/saving/saved/connection
  states and rounded popup content; update files in the original unpacked folder
  and reload the extension to retain its browser identity and token.
- **Safety:** only EXAMINE, read-only UID SEARCH/FETCH and BODY.PEEK; no SMTP,
  URL requests, tracking pixels, link previews, email changes or cloud processing.
  Links expire after 10 minutes by default, remain encrypted on disk, and are
  removed atomically when claimed. Host administrators can still read secrets.
- **Extension:** download from the signed-in settings page; Chrome loads unpacked,
  Firefox loads temporarily unless signed for permanent installation. Source is
  configured at runtime, host permission is requested for that source only,
  destination hosts are separately allowlisted, and opening always requires a click.
  No page content or browser history access. Background polling is about 30 seconds
  and browsers may defer it while suspended. Popup opening checks immediately.
- **Lifecycle:** from `hosts/server-1`, run `docker compose build email-link-helper`,
  then `docker compose up -d --no-deps email-link-helper-imap-socket email-link-helper`.
  Use a new `EMAIL_LINK_HELPER_IMAGE` tag for each image change, and keep the
  previous tagged image until both containers have migrated and a backup passes.
  Verify that the app and relay run the same image ID. Rebuilding a tag still
  used by a running container can leave its original image unavailable to backup.
  Only these new containers need restarting after app changes. A routing environment
  change requires recreating only Caddy after validation.
- **Backup:** included in the existing nightly backup through `backup.yaml`.
  `/opt/email-link-helper` retains runtime configuration, credentials, the Fernet
  key and encrypted state. The shared runner exports `state/helper.db` with
  SQLite's online backup API and excludes the live DB/journals; `run/` is excluded
  because the relay recreates its socket. Source and Caddy declarations retain
  their existing coverage. Restore the consistent database with its matching key
  and credentials, using the saved numeric owner/mode. Before starting the app,
  clear `queue` and `sessions` in the restored database so pending links and old
  web sessions are not replayed. Device-token hashes/settings are retained;
  revoke/re-pair browsers if restoring an older snapshot. The runtime recovery
  manifest is `/opt/tmp/email-link-helper-backup-plan.json`.

Run source tests with pytest and `npm test` after installing dev dependencies;
the source repo's GitHub Actions checks run on main pushes and pull requests
with a read-only token, hosted runners and no deployment secrets. Run
infrastructure checks with `bash scripts/validate-stack.sh compose`, `caddy`,
and `private`, plus the live backup coverage check.
