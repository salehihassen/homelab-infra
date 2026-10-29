# Career Ops evaluation deployment

Separate from Jobsmith: its own client key, workspace, containers and lifecycle.
Both apps use the existing ai-gateway model network; Career Ops now declares that
real dependency and waits for ai-policy-proxy readiness. It never depends on or
starts Jobsmith. Production Next.js Web UI (upstream alpha), core scripts,
Playwright Chromium and Codex CLI. No Docker socket or host agent home mount.
The UI has no login: authenticated tailnet membership/ACLs are its access
boundary. Caddy binds only the existing Tailscale addresses and strips incoming
Tailscale identity headers. Host 8890 is published only on IPv4 loopback; no
public/LAN listener is added. `CAREER_OPS_ALLOWED_ORIGINS` stays unset.

Source: https://github.com/career-ops-hq/career-ops at
`b5e4c26f21efee31559296495d794c9a9564a714` (core 1.38.0, web 0.16.0).
Node 24.18.0, Codex 0.164.0; Playwright 1.64.1 is recorded in core-package-lock.json (upstream omits a root lockfile).
The image builds the deployment core lockfile and upstream Web lockfile with `npm ci` and rebuilds the Web UI. Build
arguments, revision and image tag are recorded in `.env.example`; use a new tag
for every revision or image change, and retain the previous image for rollback.
When changing Codex version, also update the native catalog URL's client_version
in the separate provider configuration and verify `codex debug models` again.

## Opening links from other private services

Upstream's origin guard rejects `Sec-Fetch-Site: same-site` even for a normal
link clicked on a sibling hostname. Caddy removes that single header only for
GET/HEAD document navigations carrying browser user activation, outside `/api`
and `/api/*`. `Origin` remains intact for upstream's fallback check. Background
fetches, iframe loads, POST requests and API document navigations keep their
original metadata and remain blocked across origins. No extra origin allowlist
or upstream patch is needed.

Verify this boundary against a live deployment without model calls or writes:

```bash
python3 scripts/test-career-ops-ingress.py --url "https://<CAREER_OPS_DOMAIN>"
```

The live matrix checks successful links and rejected API/fetch/iframe/foreign
Origin cases, including encoded API paths. The Caddy validator also requires the
exact narrow matcher and forbids stripping Origin or other request headers.

## Workspace choice

`/opt/career-ops/workspace` is one persistent, pinned upstream Git checkout.
Both `CAREER_OPS_ROOT` and `CAREER_OPS_CODE_ROOT` point to `/workspace` inside
both containers. Every CLI/agent starts there. This deliberately uses the full
checkout fallback: upstream `web/src/app/api/run/route.ts` requires mode files
under the data root and returns HTTP 400 for data-only layouts. Its PDF/evaluate
prompts also name relative `modes/`, `templates/` and `cv.md` paths. No upstream
patch or fork is needed.

Only dependencies (`node_modules`, `web/node_modules`) and `web/.next` are
symlinks to the pinned image's `/app` tree. User files are ordinary persistent
files, including CV/profile, custom modes/templates, documents, pipeline,
applications, job descriptions, reports, output, interview prep, follow-ups,
assistant conversations and scheduled scan state/history. The whole separate
`/opt/career-ops/home` persists tool/provider settings and Codex sessions.
The dedicated CPA client key lives in `/opt/career-ops/secrets/cpa-client-key`.
Nothing seeds or replaces user files during startup. A revision mismatch fails
startup instead of mixing new core scripts with an old production Web build.

## Codex sandbox permissions

Install the stack's [dedicated AppArmor and seccomp policies](security/README.md)
before starting its containers. They permit Codex's nested bubblewrap sandbox
while retaining the non-root user, read-only container filesystem, zero host
capabilities and default-deny syscall policy. They apply only to Career Ops.
No upstream sandbox bypass, global kernel change or application fork is needed.

Run `python3 hosts/server-1/career-ops/test-sandbox.py` from the repository root
to check actual file and network enforcement without invoking a model. The
tests use disposable fixtures and remove them afterward.

Upstream's partial-restriction warning remains expected for URL-reading tasks:
Career Ops gives Codex `workspace-write` plus network access for those tasks.
The project workspace is writable; separate home files remain outside its
writable roots. This warning describes that policy and is not a sandbox-startup
failure or an authentication error.

## Initial installation

From the infrastructure repository root, inspect existing listeners and Docker
subnets first. Example subnet `10.66.18.0/24` and loopback 8890 must be unused.
Capture Jobsmith's ID, image, creation timestamp, ports, mounts and health before
and after; never print its environment values. Do not change its files.
Install the security profiles using the commands in `security/README.md` before
the Compose initialization/start commands below.

```bash
ss -ltn '( sport = :8890 )'
mkdir -p /opt/career-ops/{workspace,home/.codex}
chmod 700 /opt/career-ops /opt/career-ops/home /opt/career-ops/home/.codex
install -m 600 /dev/null /opt/career-ops/telemetry.env
# Both mounts must belong to container UID/GID 1000:1000.
install -m 600 hosts/server-1/career-ops/.env.example /opt/career-ops/compose.env
install -m 600 hosts/server-1/career-ops/routing.env.example /opt/career-ops/routing.env
# Edit runtime routing.env to set the private hostname; choose an unused subnet.
# Provision the dedicated CPA key as described below before running Compose.
install -m 600 hosts/server-1/career-ops/codex.config.toml.example /opt/career-ops/home/.codex/config.toml
bash scripts/env-files.sh link
cd hosts/server-1
docker compose build career-ops
# Run ONCE into an empty workspace. Refuses a nonempty directory.
docker compose run --rm --no-deps career-ops init
docker compose up -d --no-deps career-ops
docker compose up -d --no-deps --force-recreate career-ops-worker
```

Use the existing wildcard DNS/certificate when available; otherwise create
private DNS pointing at the same Tailscale addresses as adjacent sites.
Check `tailscale serve status` (normally no configuration), then validate the
routing before recreating only Caddy to load the new routing environment:

```bash
bash scripts/validate-stack.sh compose
bash scripts/validate-stack.sh caddy
cd hosts/server-1
docker compose up -d --no-deps caddy
```

Install the wrapper without root, keeping its source in this repository:

```bash
mkdir -p "$HOME/.local/bin"
ln -s "$(pwd)/hosts/server-1/career-ops/career-ops" "$HOME/.local/bin/career-ops"
# Run that command from the repository root; ensure ~/.local/bin is on PATH.
```

Visit `https://<CAREER_OPS_DOMAIN>` from another tailnet device. Onboard your
own CV and profile using the UI or CLI; no existing Jobsmith data is imported.

## CLI and existing CPA provider

Career Ops keeps its bundled Codex CLI as the upstream Web UI's execution
adapter. It sends model calls through the existing AI Policy Proxy to
CLIProxyAPI and its configured upstream accounts. No separate CPA service,
ChatGPT login, OAuth device flow, host auth.json copy or new upstream account is
required. Neither the host's Codex configuration nor Jobsmith's key is reused.

Before initial startup, register a **new client key** using the existing CPA
management procedure (the host helper is `/opt/cpa/cpa-client-keys.sh`):

```bash
source /opt/cpa/cpa-client-keys.sh
add_cpa_client career-ops
install -d -m 700 /opt/career-ops/secrets
install -m 600 /opt/cpa/secrets/client-key-career-ops /opt/career-ops/secrets/cpa-client-key
```

Use the helper's additive registration or the management API to add only this
key. Keep every existing client key and upstream credential. The pilot key was
registered with an additive API update and verified without restarting CPA or
the policy proxy. Its recovery/rotation copy is kept in CPA's secrets directory;
never print keys or put them in command arguments, logs or tracked files.

Install `codex.config.toml.example` into the **separate** persisted Codex home
on first setup; merge existing `[otel]` settings if already provisioned. The
custom provider uses `http://ai-policy-proxy:18443/v1` and Responses HTTP
streaming. Its command-backed authentication reads the Compose secret through a
private subprocess pipe. This works for Web workers, interactive sessions and
`docker compose exec` without exporting a bearer key into the container's static
environment or saving it in TOML. See [official custom provider configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

Choose the exact provider-prefixed model from the authenticated CPA catalog to
match the host's current model. The pilot pins that explicit route in its
private config instead of changing providers through a policy alias. The
portable example uses `ai-policy/internal`, whose provider selection follows the
existing policy catalog. Explicit concrete routes through the policy proxy are
intentional policy bypasses; provider/model changes remain a deliberate choice.

The provider's `model_catalog_url` reads CPA's native Codex metadata directly on
the same private network with the same client key. `client_version` must match
the pinned Codex version; generic OpenAI `/v1/models` output causes decode
warnings in Codex. Inference continues through the policy proxy. No WebSocket
transport is enabled on that proxy.

```bash
career-ops doctor                 # incomplete personal onboarding is expected
career-ops verify                 # verify the shared application pipeline
career-ops scan --help            # scanner; no implicit AI evaluation
career-ops pdf output/cv.html output/cv.pdf
career-ops shell                  # interactive Bash at /workspace
career-ops exec node tracker.mjs --help
career-ops exec node remember-cli.mjs codex  # persist the preferred backend
career-ops codex                  # interactive CLI through CPA; no login command
career-ops codex debug models     # authenticate/fetch model metadata; no inference
```

The wrapper preserves arguments and enables a TTY only for interactive terminals.
The UI detects Codex and shares its provider settings and secret with the CLI.
Use one small explicit assistant request to verify model serving and streaming;
never put model inference in health checks. Full evaluation still needs personal
onboarding. The dedicated container profiles enable agent sandbox tools while
leaving upstream's permission fencing unchanged.

For key rotation, register a replacement first, atomically replace only the
Career Ops secret file with mode 0600, recreate only the two Career Ops services
(to refresh the bind-mounted secret), verify model serving, then revoke only the
old Career Ops key. Do not remove a key while other clients use it.

## Scheduler and operations

```bash
cd hosts/server-1
docker compose logs --tail=60 career-ops career-ops-worker
docker compose restart career-ops
docker compose up -d --no-deps --force-recreate career-ops-worker
docker compose stop career-ops-worker career-ops
# Resume only these services:
docker compose up -d --no-deps career-ops
docker compose up -d --no-deps --force-recreate career-ops-worker
```

No OS cron installer is used. The worker invokes upstream
`node web/scripts/scheduled-jobs-runner.mjs` every 60 seconds after the preceding
invocation finishes. Each tick processes at most one due job, so multiple due
jobs drain over successive ticks. It runs without the UI open, saves history
under the shared workspace, and uses upstream store/resource locks to serialize
against manual Web UI runs. The worker joins only the Web container's PID
namespace, because upstream locks use PID liveness. After restarting/recreating
the Web container, recreate the worker too so it joins the current namespace.
This does not share the host PID namespace. A failed tick logs a failure and retries on a later
tick; upstream bounds retries and scan duration. Interrupted claims recover
through upstream's stale-lock/claim policy, which may delay recovery after a
hard kill. The heartbeat health check allows the upstream 75-minute worst case;
Docker restart policy recovers exits, but health status alone does not restart
a stuck container. Inspect scheduled scan history for success/failure/freshness.

No jobs are seeded. Create a deliberate, bounded discovery schedule in the UI
only after configuring targeting/portals. The worker runs scanners, never AI
evaluations or submissions. `career-ops scheduled` checks due jobs once manually.
Stop the worker to suspend unattended checks. Do not install upstream's cron job
as well. UI scheduler installer/status controls describe host cron, not this
managed worker; use Compose health/logs and saved run history as the worker status.

Stdout/stderr are bounded and discovered by the existing Alloy Docker pipeline.
These are operational logs, not model/tool traces. Codex native telemetry can be
configured in this deployment's private `home/.codex/config.toml` using the
existing [fleet contract](../../../docs/agent-telemetry.md); do not copy the
host's shared agent settings or provider credentials. Optional protected
`/opt/career-ops/telemetry.env` supplies exporter headers/resource attributes to
both services (create an empty 0600 file initially). Configure native `[otel]`
exporters for logs, traces and metrics in the separate persisted Codex home.
Recreate both services after changing the environment. Model telemetry and missed
scan alerts require separate verification; a healthy container proves neither.

## Backups, upgrades and rollback

`backup.yaml` includes all `/opt/career-ops` configuration, checkout/user files,
credentials and tool settings. Codex SQLite indexes/session stores are exported
using the existing online backup scanner. Disposable dependency/build links and
home caches are excluded; their targets live only in the image. The modest
source checkout is intentionally retained because custom templates can live
beside system code; excluding source directories would risk user customizations.
Git recovery metadata records the exact revision. Restic does not follow the
dependency symlinks. Existing host-wide exclusions also omit logs and caches.

Run the full repository validator, `bash scripts/check-private.sh`,
`bash scripts/env-files.sh check`, and on the host:

```bash
python3 hosts/server-1/backup/infra-backup-declared.py check --live
```

The existing nightly runner discovers this declaration automatically; no new
backup timer is installed. A coverage check proves selection, not an uploaded
snapshot or successful recovery. Restore into scratch first, overlay consistent
SQLite exports with recorded numeric ownership, then copy the workspace/home
with only Career Ops stopped. Load the saved image archive and use its recovery
image override; its dependency/build trees supply the symlink targets.
The three dependency links themselves are excluded, so recreate missing links
in the recovered workspace before starting either container:

```bash
ln -s /app/node_modules /restore/opt/career-ops/workspace/node_modules
ln -s /app/web/node_modules /restore/opt/career-ops/workspace/web/node_modules
ln -s /app/web/.next /restore/opt/career-ops/workspace/web/.next
```

If a link already exists, verify its target instead of replacing it; stop on a
collision with ordinary files. Preserve container UID/GID 1000:1000 and restore
credential directory permissions (0700/0600).

For an upgrade, keep the old image/tag and record the old workspace commit.
Refresh core-package-lock.json from the new revision's package.json with
`npm install --package-lock-only --ignore-scripts` in a clean disposable source
checkout; review the lockfile diff. Do not regenerate the upstream Web lockfile.
Build the new image under a new tag with both new pinned build arguments **before**
stopping anything. Use the same new revision in runtime compose.env. Check for
tracked user edits in the workspace (`git status --short`); if any exist, stop
and reconcile them without discarding changes. Never use `git reset --hard`,
`git clean`, `checkout --force`, upstream self-update, or `npm update` here.
Confirm a recoverable backup before accepting any data-format migration.

```bash
cd hosts/server-1
docker compose build career-ops
# Stop only the two writers before changing their code.
docker compose stop career-ops-worker career-ops
git -C /opt/career-ops/workspace fetch origin <new-pinned-commit>
git -C /opt/career-ops/workspace checkout --detach <new-pinned-commit>
docker compose up -d --no-deps career-ops
docker compose up -d --no-deps --force-recreate career-ops-worker
```

Git preserves ignored user files and refuses conflicting untracked files; do
not force it past a collision. Existing dependency/build symlinks keep their
paths and resolve to the new image. Run doctor, PDF, profile/pipeline write/read,
UI/CLI parity and restart-persistence checks again. Core and Web UI always move
together. This procedure changes source/version only, never overlays user data.

To roll back, stop only the two services, check out the recorded prior commit
(with the same clean-tree/collision checks), restore the previous image tag and
revision/Codex build arguments in compose.env, then start only those services.
Keep the current workspace, CPA client key and provider configuration. If upstream migrated data in an
incompatible way, restore a compatible snapshot into a separate workspace for
inspection before choosing any data restore; do not overwrite newer work.

## Initial rollout verification and open work

The production image, HTTPS UI/API, UI status editing, shared CLI records,
CV/profile/report/chat persistence across a Web container restart, Playwright PDF
rendering, cross-container lock liveness, and worker execution of an empty-target
discovery fixture were exercised. The fixture contained fictional data and no
external job targets or applications; it is removed after verification. Compose,
routing (including missing routing), the full validation suite, private identifier
checks and live backup coverage passed. The 2026-10-29 recovery drill subsequently
passed snapshot restore verification, Codex SQLite integrity/ownership checks,
startup from the saved image, pipeline reads and profile API writes in an
isolated container. The excluded dependency links were explicitly recreated as
documented above. A physically separate tailnet device still needs verification.

Codex is detected and upstream's permission-capability probe accepts its flags.
Docker's default policies initially blocked bubblewrap namespace/mount setup.
The dedicated profiles resolve that failure, with no-model regression tests
checking read-only enforcement, workspace writes, denied home writes, permitted
URL fetching, blocked networking and zero outer-container capabilities. No
sandbox bypass, added application capability, unconfined application profile or
global kernel-policy change is used.
The OAuth requirement was subsequently replaced with the existing CPA gateway.
A dedicated client key, custom Codex provider and native model catalog are
configured. CLI model serving and HTTPS assistant streaming are checked
separately from the still-blocked sandbox tools. Personal CV/profile onboarding
and full evaluation remain the owner's acceptance work; no authentication
provider sign-in is required.
