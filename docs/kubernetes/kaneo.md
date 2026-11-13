# Kaneo on the public cluster

A new, empty Kaneo instance, separate from the private Compose Kaneo: it shares
no data or secrets with it. It runs as Deployment `kaneo` in namespace `kaneo`
and keeps everything durable in the `kaneo` database on the
[shared PostgreSQL](postgres.md) (`postgres-rw.postgres.svc.cluster.local`).
Its `/var/lib/kaneo` and `/tmp` are temporary and not backed up. Flux starts it
only after `postgres` is Ready. SMTP, OAuth, Redis and S3 uploads are not set
up.

## Prepare the new instance

Do this during [setup](bootstrap.md) step 5, before turning on GitOps.

1. **Database password:** [Add an app](postgres.md#add-an-app) step 2 with
   `app=kaneo`. This writes `local/public-edge-inputs/kaneo/db-password`.
2. **Kaneo's Secret and settings:** the same database password, a new auth
   secret, and the login address for the first, local test:

   ```bash
   umask 077
   dir=local/public-edge-inputs/kaneo
   export KANEO_CREDENTIALS_FILE="$dir/credentials.env" KANEO_SETTINGS_FILE="$dir/settings.env"
   if [ -e "$KANEO_CREDENTIALS_FILE" ]; then
     echo "Inputs exist; inspect them, do not regenerate." >&2
   else
     printf 'POSTGRES_PASSWORD=%s\nAUTH_SECRET=%s\n' "$(cat "$dir/db-password")" \
       "$(openssl rand -hex 32)" > "$KANEO_CREDENTIALS_FILE"
     printf 'KANEO_CLIENT_URL=http://127.0.0.1:8911\n' > "$KANEO_SETTINGS_FILE"
   fi
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/apps/kaneo/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create secret generic kaneo-credentials \
     --from-env-file="$KANEO_CREDENTIALS_FILE"
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create configmap kaneo-settings \
     --from-env-file="$KANEO_SETTINGS_FILE"
   ```

3. **Check** the key names, without printing values:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo get secret kaneo-credentials \
     -o go-template='{{range $k, $v := .data}}{{$k}} {{end}}{{"\n"}}'   # AUTH_SECRET POSTGRES_PASSWORD
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo get configmap kaneo-settings -o jsonpath='{.data}{"\n"}'
   ```

4. **Copy `local/public-edge-inputs/` off the server.**

Don't edit these files in an editor: the password must not end in a newline.
To rotate the database password, change it in both Secrets, then restart
Kaneo. Changing `AUTH_SECRET` signs everyone out.

Later, to keep these inputs in Git, encrypt them with SOPS to the public
cluster's age key only. Never commit plaintext Secrets or private keys.

## Add the public HTTPS route

Only after the admin account is claimed ([testing.md](testing.md) step 3) and
a restore drill passed. The site is already in
`hosts/server-2/caddy/Caddyfile`; Compose Caddy (host network) reaches Kaneo's
ClusterIP directly, so no Kubernetes policy changes.

1. **Point Kaneo at its public address** (`https://`, no trailing slash) in
   the settings file, then apply it and restart:

   ```bash
   "${EDITOR:-nano}" "$KANEO_SETTINGS_FILE"     # KANEO_CLIENT_URL=https://<kaneo domain>
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create configmap kaneo-settings \
     --from-env-file="$KANEO_SETTINGS_FILE" --dry-run=client -o yaml \
     | kubectl --context "$PUBLIC_CONTEXT" apply -f -
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout restart deployment/kaneo
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
   ```

   From now on, login only works at the public address.
2. **Tell Caddy where Kaneo is.** Add both lines to `/opt/caddy/routing.env`
   **before** recreating Caddy. With an empty domain the Caddyfile is invalid,
   and Caddy, with every public site, fails to start:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo get service kaneo -o jsonpath='{.spec.clusterIP}{"\n"}'
   # in /opt/caddy/routing.env:
   #   KANEO_PUBLIC_DOMAIN=<kaneo domain>
   #   KANEO_K3S_UPSTREAM=<that ClusterIP>:5173
   ```

3. **Check Caddy can reach it:**

   ```bash
   upstream=<that ClusterIP>:5173
   curl --fail "http://$upstream/api/health"
   docker exec caddy wget -qO- "http://$upstream/api/health"
   ```

4. **Point DNS** for the domain at the server, so Caddy can get a certificate.
5. **Recreate only Caddy** (a reload doesn't pick up new env values):

   ```bash
   docker compose --project-directory hosts/server-2 -f hosts/server-2/compose.yaml up -d --no-deps caddy
   ```

Then run the public checks at the end of [testing.md](testing.md).

The ClusterIP changes if the Service or cluster is recreated: update
`KANEO_K3S_UPSTREAM` after any rebuild. To take Kaneo offline publicly, remove
only its two routing lines and recreate Caddy; keep its database, credentials
and recovery set.

## Capture one coherent recovery set

Use test content for the first rehearsal. Before public use, define RPO/RTO,
retention, backup scheduling and failure alerts. Keep the matching database,
stable auth secret, password and origin settings together. A raw live PostgreSQL
data-directory copy is not a native backup.

Set RECOVERY_DIR to a new private directory **outside tracked source**, and
export the input file paths from preparation. The following workstation
subshell stops only Kaneo, leaving PostgreSQL, its other databases and Compose
services running. Its cleanup resumes reconciliation even if capture fails.
Treat a failing capture as incomplete and never upload it as a valid backup.

```bash
(
  set -euo pipefail
  : "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}" "${RECOVERY_DIR:?}" "${KANEO_CREDENTIALS_FILE:?}" "${KANEO_SETTINGS_FILE:?}"
  umask 077
  mkdir -m 0700 "$RECOVERY_DIR"
  cleanup_capture() {
    flux --context "$PUBLIC_CONTEXT" resume kustomization postgres || true
    flux --context "$PUBLIC_CONTEXT" resume kustomization kaneo || true
    flux --context "$PUBLIC_CONTEXT" resume kustomization flux-system || true
    kubectl --context "$PUBLIC_CONTEXT" -n flux-system annotate fluxinstance/flux \
      fluxcd.controlplane.io/reconcile- || true
  }
  trap cleanup_capture EXIT
  kubectl --context "$PUBLIC_CONTEXT" -n flux-system annotate fluxinstance/flux \
    fluxcd.controlplane.io/reconcile=disabled --overwrite
  flux --context "$PUBLIC_CONTEXT" suspend kustomization flux-system
  flux --context "$PUBLIC_CONTEXT" suspend kustomization kaneo
  flux --context "$PUBLIC_CONTEXT" suspend kustomization postgres
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo scale deployment/kaneo --replicas=0
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo wait --for=delete pod \
    -l app.kubernetes.io/name=kaneo --timeout=120s
  primary="$(kubectl --context "$PUBLIC_CONTEXT" -n postgres get pod -o name \
    -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary)"
  kubectl --context "$PUBLIC_CONTEXT" -n postgres exec "$primary" -c postgres -- \
    pg_dump -d kaneo --format=custom > "$RECOVERY_DIR/kaneo.dump"
  cp "$KANEO_CREDENTIALS_FILE" "$RECOVERY_DIR/credentials.env"
  cp "$KANEO_SETTINGS_FILE" "$RECOVERY_DIR/settings.env"
  cp "$(dirname "$KANEO_CREDENTIALS_FILE")/db-password" "$RECOVERY_DIR/db-password"
)
flux --context "$PUBLIC_CONTEXT" reconcile kustomization postgres --with-source
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
release and matching recovery credentials. Pause FluxInstance reconciliation
first as in the capture procedure, then suspend root, app and database
reconciliation, scale candidate Kaneo to zero, and wait for its Pod to disappear. Keep candidate
PostgreSQL running.

Review the dump with pg_restore --list. The target database must be empty:
Kaneo creates schema on startup. In the isolated candidate only, create
`kaneo-db-password` from the recovered `db-password`
([Add an app](postgres.md#add-an-app) step 2) and let Flux create the empty
`kaneo` database, then import the custom-format dump as objects owned by
`kaneo`:

```bash
primary="$(kubectl --context "$RESTORE_CONTEXT" -n postgres get pod -o name \
  -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary)"
kubectl --context "$RESTORE_CONTEXT" -n postgres exec -i "$primary" -c postgres -- \
  pg_restore -d kaneo --role=kaneo --no-owner --no-acl --exit-on-error < "$RECOVERY_DIR/kaneo.dump"
```

There is no application-file archive to restore. Kaneo regenerates its runtime
files on startup. If S3 uploads are added later, include the object store and
its matching database records in a revised recovery procedure.

RESTORE_CONTEXT must select an isolated candidate kubeconfig; export its
KUBECONFIG explicitly. Never mount or edit a live PostgreSQL volume. Restore the
matching auth secret/password, adapt only the candidate browser origin for
local access, resume database, app and root reconciliation and verify login,
test project, task and restart survival. A successful SQL import alone is not an application
restore drill.

To withdraw the new service, remove only its new Caddy route and preserve its
`kaneo` database, credentials and recovery set. Keep existing private Compose Kaneo
running as its own instance. After public writes, any redeployment needs the
new instance's current data, not an old private database. Never delete PVCs to roll back an
image; retained PVCs do not survive disk failure or careless K3s uninstall.
