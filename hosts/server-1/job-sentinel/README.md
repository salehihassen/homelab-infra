# Job Sentinel

Experimental service at `JOB_SENTINEL_DOMAIN`, over Tailscale only.
Images: `JOB_SENTINEL_API_IMAGE` and `JOB_SENTINEL_WEB_IMAGE`, published to GHCR
by the Images workflow on the fork's deploy branch (built from its
`deploy/api.Dockerfile` and `deploy/web.Dockerfile`; tags `sha-<commit>`,
the branch name, and `latest`). Pin a digest in the env file.
Runtime: /opt/job-sentinel. Config: config/.env; app state: data/.

From hosts/server-1: `docker compose pull job-sentinel-api job-sentinel-web`, then
`docker compose up -d job-sentinel-api job-sentinel-web`;
stop with `docker compose stop job-sentinel-web job-sentinel-api`; logs with
`docker compose logs --tail=100 job-sentinel-api job-sentinel-web`.

Caddy handles TLS with Porkbun DNS-01 and binds to the host's Tailscale addresses.
UI is loopback 8902; API is loopback 8903. /api/*, /health, /docs and
/openapi.json go to the API; other routes go to the UI. No Tailscale Serve.
The UI image is built with an empty API base, so it calls /api on its own origin;
this routing is what connects the two, and no domain is baked into the image.
AUTH_MODE=off follows the trusted-tailnet single-user convention.
No watcher or outbound notification service is started; DRY_RUN=true.
Portal sessions and credentials must be supplied before portal scraping works.
AI provider/key settings can be saved in the UI; no provider was provisioned.

The API image (Python 3.12, Playwright Chromium, Tectonic) starts via the fork's
deploy/serve.py, which points Pydantic's config file at /config/.env before
importing the API, so Settings updates atomically replace the file on the
config bind mount. State paths resolve to /app/data (editable install keeps the
upstream repo-root path assumptions).

Backups: included in the nightly backup since 2026-10-05 (see backup.yaml):
config/ and data/, with an SQLite online copy of data/jobs.db; logs are excluded.
