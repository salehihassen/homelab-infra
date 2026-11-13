# Validation and Git hooks

```bash
bash scripts/validate-stack.sh           # compose + deps, Caddy, Alloy, Prometheus rules/tests, Loki, private identifiers
bash scripts/validate-stack.sh compose   # fast: Compose config + stack dependency check
bash scripts/check-private.sh            # no real host names, domains or paths in tracked files
bash scripts/validate-kubernetes.sh      # render roots/children, schemas and migration safety contracts
INFRA_HOST=server-2 bash scripts/validate-stack.sh all  # independent host contracts
python3 backup/infra-backup-declared.py --host server-1 check --live   # backup coverage (on the host)
```

Validation uses Docker Compose **v5.5.1**, matching CI. If your installed version
differs, `validate-stack.sh` automatically uses a checksum-verified copy cached
under `~/.cache/homelab-infra/` for that command only (Linux x86_64). It never
replaces the host's Compose plugin; ordinary `docker compose up` keeps using
your installed version. The pin makes example-only rendering reproducible and
avoids older versions reading private env files despite `--no-env-resolution`.
The Caddy check enforces
tailnet-only proxy listeners, Loki's authenticated push-only boundary, and the
CPA management denial. Gitleaks runs in CI and in the Git hooks. Enable the
hooks once per clone with `git config core.hooksPath .githooks`:

- **pre-commit**: Gitleaks, env files and private identifiers on the staged
  files, then `validate-stack.sh compose` on the staged snapshot; Kubernetes
  changes also run the manifest suite on that exact snapshot.
- **pre-push**: private identifiers and Gitleaks on every commit being pushed
  (a later fix does not unpublish an earlier commit), then the full
  `validate-stack.sh` on each pushed tip.

The hooks run `scripts/validate-snapshot.sh`, which validates exactly what is
staged or pushed. It shares the automatic test-tool selection in
`scripts/with-validation-compose.sh` with working-tree validation.
