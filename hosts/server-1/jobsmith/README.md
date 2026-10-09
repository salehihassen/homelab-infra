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

## Updating the image

The actual image reference stays in the untracked `.env` symlink target;
keep `.env.example` generic. After the application workflow succeeds, pull the
commit-tagged image, verify its revision label matches the tested application
commit, and pin both its tag and registry digest in `JOBSMITH_IMAGE`.

Before recreation, export SQLite through its online backup API and validate
that export, then preserve configuration and the previous runtime env file in
a protected backup directory. Avoid printing resolved Compose configuration or
container environments, since they can contain credentials and private routing
values. From the host directory, validate with `docker compose config --quiet`,
then use `docker compose up -d --no-deps --wait jobsmith`.

Verify the running revision, healthy status, unchanged data mounts, loopback
port binding, and unauthenticated API rejection. Keep image pins and private
hostnames out of tracked files; run the private-identifier and secret checks on
the staged changes. Updating this application does not require a Caddy restart.
