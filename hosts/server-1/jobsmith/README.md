# jobsmith

Jobsmith job-application assistant (`JOBSMITH_DOMAIN`).

- **Image:** published to GHCR by the application's GitHub Actions workflow.
  Set `JOBSMITH_IMAGE` in `.env` to the published image reference, preferably
  pinned by digest. From the host directory, run `docker compose pull jobsmith`,
  then `docker compose up -d --no-deps jobsmith`.
- **Requires:** ai-gateway (client key `/opt/jobsmith/secrets/cpa-client-key`).
- **Network:** its own `jobsmith` bridge for browser-automation egress, plus
  `cpa_policy_model`.
- **Data:** `/opt/jobsmith/{config,data,resumes,sessions,failed_screenshots,.browser-profile}`.
- **Backup:** `/opt/jobsmith` except the source worktree, with an SQLite online
  copy of `jobsmith.db`.
