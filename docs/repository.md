# Repository layout and conventions

How this repository is organised, how to deploy your own copy, and how a
deployment's identity stays out of Git. Day-to-day commands are in the
[runbook](../RUNBOOK.md).

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
hosts/server-2/            # independent project; active apps and AIO controller only
  PLAYBOOK.md             # deployment, checks, backups, restore and rollback
inventory.yaml            # service -> host alias, URL variable, data, backup, exposure
RUNBOOK.md                # rebuild, restore, secrets, day-to-day operations
docs/                     # backups format, agent telemetry, archived services
scripts/                  # validation, dependency/backup/private-identifier checks
kubernetes/               # public Flux root, new Kaneo instance and PostgreSQL
provisioning/k3s/          # reviewed-by-operator host examples; no remote installer
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

## Stack dependencies

Stacks share infrastructure only through declared contracts. A stack that uses
Postgres joins the `postgres_network`, waits for `postgres` to be healthy, and
lists `postgres` under `x-stack.requires`; the same pattern applies to the AI
gateway (`cpa_policy_model` + `ai-policy-proxy`). `scripts/check-stacks.py`
fails if a stack uses another stack's network, service, or secret without
requiring it, or requires a stack it does not use. Current graph:

| Stack | Requires |
| --- | --- |
| postgres, ai-gateway, caddy, komodo, home-automation, protonmail-bridge, countdown, metube, job-sentinel, media-extract, kaneo | - |
| quotes, forgejo, langfuse | postgres |
| observability | langfuse |
| jobsmith, condenseit, speakable | ai-gateway |
| email-assist | postgres, ai-gateway, protonmail-bridge |
