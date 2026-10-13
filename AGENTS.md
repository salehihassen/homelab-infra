# Notes for coding agents

This repository is **public**. It describes a real deployment through neutral
aliases (`server-1`, `*.private.example`), and a private-identifier check keeps
it that way. A hostname, domain, IP address, username, path, email address or
secret that reaches a commit is published. Removing it afterwards means
rewriting history and force-pushing.

## Before every commit

1. Make sure the hook is on: `git config core.hooksPath` must print
   `.githooks`. If it doesn't, run `git config core.hooksPath .githooks`. The hook
   runs gitleaks, refuses env files and runs the private-identifier check on
   staged files. Never bypass it (`--no-verify`).
2. Stage only the files you changed, by name. Avoid `git add -A` and `git add .`:
   the working tree can hold the owner's uncommitted work or untracked notes.
3. Run the checks yourself and read the output:
   ```bash
   bash scripts/check-private.sh            # real names, domains, IPs, paths (patterns in local/, untracked)
   bash .githooks/pre-commit                # what the hook will run on the staged files
   bash scripts/validate-stack.sh           # full suite; or `compose`, `caddy`, `alloy`, `loki`, `private`
   python3 hosts/server-1/backup/infra-backup-declared.py check --live   # on the host, after stack changes
   ```
   `validate-stack.sh` requires the Docker Compose version pinned in CI. If the
   host's version differs, run the individual checks and say so; do not edit
   the pin.
4. If any check fails, fix the content. Do not weaken patterns, rules or
   allowlists to make it pass.

## What never goes in a tracked file

- Env files (`.env`, `*.env`). Only `*.env.example` templates with placeholder
  values (`replace-with-...`, `*.private.example`) are tracked. Real values live
  under `/opt` on the host and map to the repo through `hosts/<host>/env-files.tsv`.
- Secrets of any kind, including password hashes (Caddy `basic_auth` hashes
  belong in `routing.env` or untracked files under `/opt`). Keep placeholders
  low-entropy, or gitleaks flags them.
- Real host names, domains, Tailscale or LAN addresses, usernames, email
  addresses and home-directory paths. Use aliases and variables
  (`JOBSMITH_DOMAIN`, `https://<name>.<PRIVATE_ZONE>`, `/home/<user>`).
  Machine-specific values go in untracked files: `local/site.md`,
  `local/private-patterns.txt`, `hosts/<host>/backup/host.local.yaml`.
- Commit messages and file names count too; the check scans paths.

## Working with secrets on the host

- Read secrets from their files instead of printing them. Redact values when
  you must inspect env files (`grep -o '^[A-Z_]*='`).
- New credentials: generate them into 0600 files under `/opt/<stack>/`, and
  reference them as Compose `secrets:` or service `env_file:`. Never use
  inline `environment:` values in tracked YAML.
- When something sensitive was already committed, stop and tell the owner.
  Only rewrite history or force-push when they ask.

See `README.md` (layout, validation), `RUNBOOK.md` (operations) and
`docs/backups.md` (what gets backed up).
