# Public service host

This host uses the existing `server` Compose project identity. It manages only
the currently retained applications and a remote Alloy log shipper. Nextcloud
AIO exclusively manages its children. The host's real identity is recorded in
ignored `local/site.md`, never in tracked paths, configuration or commands.

Runtime settings and credentials live in `/opt/<stack>`; `.env` and
`routing.env` are ignored links listed in `env-files.tsv`. Existing data remains
at paths selected through private interpolation settings. External volume names
are explicit and must already exist; missing binds fail rather than silently
creating empty directories. The active services are in [inventory.yaml](../../../inventory.yaml).

Public HTTPS is intentional on this host. Public routing is validated separately
from server-1's tailnet-only rules; server-1 protections remain enforced.
Example backend listeners are loopback-only. Adoption preserves existing private
IPv4/IPv6 listener choices; review direct consumers before changing those inputs.

Run `INFRA_HOST=server-2 bash scripts/validate-stack.sh all` from the repo root.
Use [PLAYBOOK.md](PLAYBOOK.md) for deployment, verification, updates, logging,
backup and recovery. No private domain, address, username or credential belongs
in this directory, including image names or build paths that identify the owner.
