# Homelab infra

Deployment source of truth for a small self-hosted fleet: Docker Compose,
Caddy, DNS-01 certificates, and Tailscale. Every service is reachable only
from the tailnet at `https://<name>.<PRIVATE_ZONE>`; the zone and each
service's hostname are set in env files, never in Git.

Hosts appear under neutral aliases (`server-1`, ...). Application source lives
in its own repositories; images are referenced through each stack's env file.
Runtime state and secrets stay on each host (under `/opt`), never in Git; the
nightly restic backup is their only other copy.

## Layout

```
hosts/server-1/
  compose.yaml            # name: opt; includes every stack below
  .env -> /opt/.env       # shared TZ only; template: .env.example
  <stack>/
    .env -> /opt/...      # local interpolation settings; template: .env.example
    routing.env -> /opt/...  # optional Caddy routing/auth inputs, when applicable
    compose.yaml          # services, networks, secrets; x-stack.requires
    site.caddy            # the stack's Caddy sites (if any)
    backup.yaml           # what the nightly backup captures
    README.md             # purpose, dependencies, data, restore notes
  caddy/Caddyfile         # global options, wildcard cert, host-level sites
  backup/                 # backup declaration, runner scripts, systemd units
  env-files.tsv           # which repo symlink points at which /opt env file
inventory.yaml            # service -> host alias, URL variable, data, backup, exposure
RUNBOOK.md                # rebuild, restore, secrets, day-to-day operations
docs/                     # backups format, archived services
scripts/                  # validation, dependency/backup/private-identifier checks
local/                    # untracked: real host names, domains, scan patterns
```

## Deploying your own copy

Copy `hosts/server-1/.env.example` to `.env`, each stack's `.env.example` to
`.env`, and each `routing.env.example` to `routing.env`. Replace placeholder
domains, IPs, image references, source paths, devices, secrets and the Loki
ingress auth hash with your own values. Provision the runtime data and
application credentials described in each stack's README and `backup.yaml`;
the example env files describe interpolation/routing settings, not restored
application data. Existing deployment files must not be overwritten with
examples. On a running host the env files are symlinks into `/opt`, mapped in
`hosts/server-1/env-files.tsv` (`bash scripts/env-files.sh link` recreates them).

## Keeping a deployment's identity out of Git

Anything that identifies a real deployment stays in untracked files:

- Env files under `/opt`: domains, image references, source paths, secrets.
- `hosts/<host>/backup/host.local.yaml`: personal backup paths.
- `/etc/infra-backup/backup.env`: restic repository and host tag.
- `local/site.md`: which alias is which real machine, DNS and backup details.
- `local/private-patterns.txt`: extended regexes for real host names, domains,
  usernames and addresses. `scripts/check-private.sh` (run by the pre-commit
  hook and `validate-stack.sh`) fails if any tracked file or path matches.

## Everyday commands

```bash
cd hosts/server-1
docker compose ps
docker compose up -d <service>          # apply a change to one service
docker compose up -d --build <service>  # rebuild a locally built image
docker compose logs --tail=100 <service>
```

Never run `docker compose down` for the whole project, and never add `-v`.

## Stack dependencies

Stacks share infrastructure only through declared contracts. A stack that uses
Postgres joins the `postgres_network`, waits for `postgres` to be healthy, and
lists `postgres` under `x-stack.requires`; the same pattern applies to the AI
gateway (`cpa_policy_model` + `ai-policy-proxy`). `scripts/check-stacks.py`
fails if a stack uses another stack's network, service, or secret without
requiring it, or requires a stack it does not use. Current graph:

| Stack | Requires |
| --- | --- |
| postgres, ai-gateway, caddy, observability, komodo, home-automation, protonmail-bridge, countdown, metube, job-sentinel | - |
| quotes, forgejo | postgres |
| jobsmith, condenseit, speakable | ai-gateway |
| email-assist | postgres, ai-gateway, protonmail-bridge |

## Validation

```bash
bash scripts/validate-stack.sh           # compose + deps, Caddy build/validate/routing, Alloy, Loki, private identifiers
bash scripts/validate-stack.sh compose   # fast: Compose config + stack dependency check
bash scripts/check-private.sh            # no real host names, domains or paths in tracked files
python3 hosts/server-1/backup/infra-backup-declared.py check --live   # backup coverage (on the host)
```

Requires Docker Compose **v5.5.1** (pinned in CI). The Caddy check enforces
tailnet-only proxy listeners, Loki's authenticated push-only boundary, and the
CPA management denial. Gitleaks runs in CI and as a pre-commit hook. Enable
the hook once per clone with `git config core.hooksPath .githooks`.
