# New public Kaneo: inputs, HTTPS and recovery

This is an independent, empty instance. Existing private Compose Kaneo and every
other Compose service keep their current data, credentials and routes. Do not
import personal projects or reuse private Kaneo's database/auth secret for this
pilot. This change adds Kaneo, dedicated PostgreSQL 16 and two retained local
PVCs; SMTP, OAuth, Redis, uploads/S3 integrations and other services are not added.

## Prepare the new instance

Complete this after installing Flux controllers but **before applying the root
Git sync** in [bootstrap](bootstrap.md). All commands below use the workstation's
public kubeconfig and explicitly selected PUBLIC_CONTEXT.

1. Inspect the data disk and local-path provisioner path on the public server
   (normally `/var/lib/rancher/k3s/storage`). Configure it before PVC allocation
   if a different disk is required. Check capacity, inodes, ownership and
   monitoring. PVC size requests do not enforce local-path disk quotas.
2. Export PUBLIC_STORAGE_NODE to the inspected public node's name. Confirm the
   cluster label and apply storage ownership to exactly that one node:

   ```bash
   : "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}" "${PUBLIC_STORAGE_NODE:?}"
   kubectl --context "$PUBLIC_CONTEXT" get nodes --show-labels
   kubectl --context "$PUBLIC_CONTEXT" label node "$PUBLIC_STORAGE_NODE" homelab.example/kaneo-storage=true
   kubectl --context "$PUBLIC_CONTEXT" get nodes -l homelab.example/kaneo-storage=true
   ```

   Expect exactly one node, also labeled homelab.example/cluster=public-edge.
   Both Deployments are pinned there. This is one-node persistence: losing the
   server takes this app and its database offline.
3. Create fresh inputs in ignored/private storage. The following example uses
   the repository's ignored local directory, creates mode-0600 files and refuses
   to overwrite existing inputs. Save recovery copies in private storage too.
   Run on the workstation from the repo root; it does not print secrets.

   ```bash
   umask 077
   export KANEO_INPUT_DIR=local/public-edge-inputs
   python3 - <<'PY'
   import os
   from pathlib import Path
   import secrets

   target = Path(os.environ['KANEO_INPUT_DIR'])
   target.mkdir(mode=0o700, parents=True, exist_ok=True)
   credentials = target / 'credentials.env'
   settings = target / 'settings.env'
   if credentials.exists() or settings.exists():
       raise SystemExit('Inputs already exist; inspect privately, do not regenerate.')
   credentials.write_text(
       f'POSTGRES_PASSWORD={secrets.token_hex(32)}\n'
       f'AUTH_SECRET={secrets.token_hex(32)}\n')
   settings.write_text('KANEO_CLIENT_URL=http://127.0.0.1:8911\n')
   credentials.chmod(0o600)
   settings.chmod(0o600)
   PY
   export KANEO_CREDENTIALS_FILE="$KANEO_INPUT_DIR/credentials.env"
   export KANEO_SETTINGS_FILE="$KANEO_INPUT_DIR/settings.env"
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/apps/kaneo/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create secret generic kaneo-credentials \
     --from-env-file="$KANEO_CREDENTIALS_FILE"
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create configmap kaneo-settings \
     --from-env-file="$KANEO_SETTINGS_FILE"
   ```

   The initial origin deliberately matches the loopback test port. Both
   Deployments use the dedicated password; database/user names are kaneo.
   Changing a Secret after PostgreSQL initialization does not rotate its stored
   password. Coordinate SQL rotation and app restart if rotation is needed.
4. Save these inputs and Flux/age keys in independently recoverable storage.
   Plan native DB + app-file backup into the existing Restic/B2 system; prove
   restore with test data before accepting valuable writes. Existing Compose
   backup declarations do not automatically capture Kubernetes state.
5. Return to bootstrap step 4 and start reconciliation. Work through
   [testing.md](testing.md) to claim the initial administrator, verify auth,
   CRUD, restart survival and Flux drift repair before public exposure.

Inputs initially stay operator-managed. To adopt SOPS later, add encrypted
resources to the public app's Kustomize path and encrypt only to its public
cluster age recipient. The app reconciler already references sops-age. Protect
both data/stringData; ConfigMap hostnames are private deployment inputs too.
Never commit private keys or plaintext runtime resources.

## Add only the new public HTTPS route

After local acceptance and a restore drill:

1. Select a new public hostname. Set KANEO_CLIENT_URL in the private settings
   file to its final HTTPS origin (no trailing slash). Apply the updated
   ConfigMap and restart Kaneo so the new origin takes effect:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create configmap kaneo-settings \
     --from-env-file="$KANEO_SETTINGS_FILE" --dry-run=client -o yaml \
     | kubectl --context "$PUBLIC_CONTEXT" apply -f -
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout restart deployment/kaneo
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo get service kaneo
   ```

   Login now needs that HTTPS origin. Loopback remains useful for health checks.
2. The public Caddy container uses host networking. On this same public node it
   can reach Kaneo's actual ClusterIP:5173 through K3s service rules. Set private
   KANEO_K3S_UPSTREAM to that address/port and KANEO_PUBLIC_DOMAIN to the chosen
   hostname in `/opt/caddy/routing.env`, already loaded by Compose Caddy.
   From the VPS and the actual Caddy container, request the upstream's
   `/api/health` before changing routes. The node-to-local-Pod path is permitted
   by Kubernetes policy semantics; this host remains an administrative trust
   boundary. Do not add an allow-all policy to mask a failed path test.
3. Review the opt-in `provisioning/k3s/caddy-kaneo.caddy.example` block and add
   only that new site to the public host's Caddyfile. Do not replace existing
   routes. Arrange DNS and certificate issuance. Validate the full config and
   existing routes using the server-2 playbook/tooling before applying it.
   The template is not imported by this branch's active Compose configuration.
4. Recreate **only Caddy** to load added environment values; reload alone does
   not refresh its process environment. Follow the existing server-2 playbook.
   From the repository root on that host, the targeted Compose operation is:

   ```bash
   docker compose --project-directory hosts/server-2 -f hosts/server-2/compose.yaml up -d --no-deps caddy
   ```

   Run the checks in testing.md against public HTTPS and every pre-existing
   public service. No direct 5173/5432 listener should be exposed.

Do not assume the ClusterIP stays constant after deleting/recreating its Service
or cluster. Update the private upstream during recovery. This same-host bridge
is for the single-node pilot. Redundant ingress and external traffic failover
belong to a later change; Caddy here is a single entry point.

## Capture one coherent recovery set

Use test content for the first rehearsal. Before public use, define RPO/RTO,
retention, backup scheduling and failure alerts. Keep matching database, files,
stable auth secret, password and origin settings together. A raw live PostgreSQL
data-directory copy is not a native backup.

Set RECOVERY_DIR to a new private directory **outside tracked source**, and
export the input file paths from preparation. The following workstation
subshell stops only the new Kubernetes app, leaving its database and Compose
services running. Its cleanup resumes reconciliation even if capture fails.
Treat a failing capture as incomplete and never upload it as a valid backup.

```bash
(
  set -euo pipefail
  : "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}" "${RECOVERY_DIR:?}" "${KANEO_CREDENTIALS_FILE:?}" "${KANEO_SETTINGS_FILE:?}"
  umask 077
  mkdir -m 0700 "$RECOVERY_DIR"
  cleanup_capture() {
    kubectl --context "$PUBLIC_CONTEXT" -n kaneo delete pod kaneo-data-helper --ignore-not-found || true
    flux --context "$PUBLIC_CONTEXT" resume kustomization kaneo || true
    flux --context "$PUBLIC_CONTEXT" resume kustomization flux-system || true
  }
  trap cleanup_capture EXIT
  flux --context "$PUBLIC_CONTEXT" suspend kustomization flux-system
  flux --context "$PUBLIC_CONTEXT" suspend kustomization kaneo
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo scale deployment/kaneo --replicas=0
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo wait --for=delete pod \
    -l app.kubernetes.io/name=kaneo --timeout=120s
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo exec deployment/kaneo-postgres -- \
    pg_dump -U kaneo -d kaneo --format=custom > "$RECOVERY_DIR/kaneo.dump"
  kubectl --context "$PUBLIC_CONTEXT" apply -k kubernetes/operations/kaneo-data
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo wait --for=condition=Ready pod/kaneo-data-helper --timeout=120s
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo exec kaneo-data-helper -- \
    tar -C /recovery -cf - . > "$RECOVERY_DIR/kaneo-files.tar"
  cp "$KANEO_CREDENTIALS_FILE" "$RECOVERY_DIR/credentials.env"
  cp "$KANEO_SETTINGS_FILE" "$RECOVERY_DIR/settings.env"
)
flux --context "$PUBLIC_CONTEXT" reconcile kustomization kaneo --with-source
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
```

Keep the private input files synchronized with any credential/origin changes
before capture. Check capture success and resumed application health explicitly; cleanup errors
must be repaired. Include the complete set in encrypted Restic storage using a
separately configured host capture/transfer job. No scheduler or backup account
is silently installed by this branch. Capture K3s state separately as described
in bootstrap.md; Git alone does not preserve credentials or application data.

## Restore drill and retirement

Restore to an isolated fresh candidate with **no public route**, never over the
running instance or existing Compose Kaneo. Use the same pinned app/PostgreSQL
release and matching recovery credentials. Suspend root and app reconciliation,
scale candidate Kaneo to zero, and wait for its Pod to disappear. Keep candidate
PostgreSQL running.

Review the dump with pg_restore --list. The target database must be empty:
Kaneo creates schema on startup. In the isolated candidate only, prepare a fresh
empty database owned by kaneo, then import the custom-format dump:

```bash
kubectl --context "$RESTORE_CONTEXT" -n kaneo exec -i deployment/kaneo-postgres -- \
  pg_restore -U kaneo -d kaneo --no-owner --no-acl --exit-on-error < "$RECOVERY_DIR/kaneo.dump"
```

Use the restricted helper against the candidate PVC, while candidate Kaneo is
stopped. Inspect archive relative paths before extraction. Files must land at
the PVC root, owned by UID/GID 1001:

```bash
kubectl --context "$RESTORE_CONTEXT" apply -k kubernetes/operations/kaneo-data
kubectl --context "$RESTORE_CONTEXT" -n kaneo wait --for=condition=Ready pod/kaneo-data-helper --timeout=120s
kubectl --context "$RESTORE_CONTEXT" -n kaneo exec -i kaneo-data-helper -- \
  tar -C /recovery -xf - --no-same-owner < "$RECOVERY_DIR/kaneo-files.tar"
kubectl --context "$RESTORE_CONTEXT" -n kaneo delete pod kaneo-data-helper
```

RESTORE_CONTEXT must select an isolated candidate kubeconfig; export its
KUBECONFIG explicitly. Never mount/edit a live PostgreSQL PVC. Restore the
matching auth secret/password, adapt only the candidate browser origin for
local access, resume its app/root reconciliation and verify login, test project,
task, files/features in use and restart survival. A successful SQL import alone
is not an application restore drill.

To withdraw the new service, remove only its new Caddy route and preserve its
PVCs/credentials/recovery set. Keep existing private Compose Kaneo running as
its own instance. After public writes, any redeployment needs the new instance's
current data, not an old private database. Never delete PVCs to roll back an
image; retained PVCs do not survive disk failure or careless K3s uninstall.
