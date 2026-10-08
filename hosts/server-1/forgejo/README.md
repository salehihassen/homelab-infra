# forgejo

Forgejo git forge (`FORGEJO_DOMAIN`; git over SSH on port 2222) and its CI:
an isolated, privileged Docker-in-Docker daemon (`forgejo-docker`) and the
runner. Jobs never touch the host Docker daemon; only run trusted workflows.

- **Requires:** postgres (database `forgejo`; connection settings in
  `/opt/forgejo/forgejo.env`).
- **Networks:** `forgejo` (runner <-> API, and Forgejo's egress), `forgejo_ci`
  (fixed subnet; job containers reach the runner cache), `postgres_network`.
- **Data:** `/opt/forgejo` (an encrypted SATA logical volume) and
  `/opt/forgejo-runner/{data,certs,secrets}`. The CI daemon's
  `/opt/forgejo-runner/docker` is cache only.
- **Backup:** repositories and metadata must agree, so the nightly job pauses
  `forgejo` and `forgejo-runner` (about a second), dumps the `forgejo`
  database, copies `/opt/forgejo/data` into staging, then resumes them.
