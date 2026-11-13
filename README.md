# Homelab infra

> [!IMPORTANT]
> **This repo is migrating from Docker Compose to K3s, one service at a time.**
> Most services still run in Compose. The public cluster on server-2 runs Flux,
> PostgreSQL and a new Kaneo instance; the private cluster comes later. Layout
> and instructions will keep changing. If you use this repo, pin a known-good
> commit and review changes before updating your own deployment.

Deployment source of truth for a small self-hosted fleet. Hosts appear under
neutral aliases (`server-1`, `server-2`); real names, domains and secrets live
only on the hosts, never in Git.

## Where to look

| I want to… | Read |
| --- | --- |
| Understand the layout or deploy my own copy | [docs/repository.md](docs/repository.md) |
| Run, change or restore a Compose service | [RUNBOOK.md](RUNBOOK.md) |
| Operate the public host (server-2) | [hosts/server-2/PLAYBOOK.md](hosts/server-2/PLAYBOOK.md) |
| Validate changes and set up the Git hooks | [docs/validation.md](docs/validation.md) |
| Know what is backed up | [docs/backups.md](docs/backups.md) |
| Set up the K3s cluster | [docs/kubernetes/bootstrap.md](docs/kubernetes/bootstrap.md) |
| Test it, or add an app | [testing.md](docs/kubernetes/testing.md), [postgres.md](docs/kubernetes/postgres.md), [kaneo.md](docs/kubernetes/kaneo.md) |
| Understand the K3s design | [kubernetes/README.md](kubernetes/README.md), [architecture.md](docs/kubernetes/architecture.md) |

## Before your first commit

```bash
git config core.hooksPath .githooks   # gitleaks + private-identifier checks
```

This repo is public. See [AGENTS.md](AGENTS.md) for what must never be
committed.
