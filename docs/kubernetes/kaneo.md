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

A recovery set is a native database dump plus the credentials and settings
that match it, taken together. A copy of PostgreSQL's data directory is not
one. Captures are manual: nothing schedules them or copies them off the
server yet, and server-2's nightly Restic backup covers Compose only.

Run this on the server, from the repo root, with `KUBECONFIG` and
`PUBLIC_CONTEXT` exported. If you ever changed a Secret or ConfigMap by hand,
update the input files to match first. Then point at the inputs and a new
private directory outside the repo:

```bash
dir=local/public-edge-inputs/kaneo
export KANEO_CREDENTIALS_FILE="$dir/credentials.env" KANEO_SETTINGS_FILE="$dir/settings.env"
mkdir -p -m 0700 ~/k3s-recovery
export RECOVERY_DIR=~/k3s-recovery/kaneo-$(date +%F)   # must not exist yet
```

The capture stops only Kaneo, for about 30 seconds; the public site answers
502 meanwhile. PostgreSQL, its other databases and Compose keep running. If
any step fails, the cleanup still resumes Flux, and the capture is incomplete:
never keep it as a backup.

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

**Good looks like:** the last line says `successfully rolled out`;
`ls -l "$RECOVERY_DIR"` lists `kaneo.dump`, `credentials.env`,
`settings.env` and `db-password`, all `-rw-------`; and
`flux --context "$PUBLIC_CONTEXT" get kustomizations` shows every one Ready and
none suspended.

Then **copy the directory off the server**, to encrypted storage you control:
`scp -r <server-2>:k3s-recovery/kaneo-<date> .` from your laptop.

K3s's own state is captured separately; see [setup](bootstrap.md),
Maintenance.

## Restore drill

Last drill: 2026-11-13. The restored tables matched live row for row, and
login, reads, writes and restarts all worked.

Restore into a throwaway cluster on another machine, never over the live
instance. That machine needs Docker, [k3d](https://k3d.io) and kubectl, the
repo at the commit the live cluster runs, and the recovery set in a private
directory. The candidate has no Flux and no public route.

1. **Create the candidate**, on the server's K3s version, with its own
   kubeconfig:

   ```bash
   export RECOVERY_DIR=<the recovery set> RESTORE_CONTEXT=k3d-kaneo-restore
   export KUBECONFIG=~/kaneo-restore.kubeconfig
   k3d cluster create kaneo-restore --image rancher/k3s:v1.37.2-k3s1 --no-lb \
     --api-port 127.0.0.1:6550 --kubeconfig-update-default=false \
     --k3s-arg '--disable=traefik@server:0' --k3s-arg '--disable=servicelb@server:0' \
     --k3s-arg '--node-label=homelab.example/cluster=public-edge@server:0'
   (umask 077; k3d kubeconfig get kaneo-restore > "$KUBECONFIG")
   k() { kubectl --context "$RESTORE_CONTEXT" "$@"; }
   k label node k3d-kaneo-restore-server-0 homelab.example/local-storage=true
   ```

2. **Install the database** from the repo, with the recovered password. The
   operator needs `--server-side`: its CRDs are too large for a normal apply.

   ```bash
   k apply --server-side -k kubernetes/infrastructure/cloudnative-pg
   k -n cnpg-system rollout status deployment/cnpg-controller-manager --timeout=300s
   k apply -f kubernetes/databases/postgres/namespace.yaml
   k -n postgres create secret generic kaneo-db-password --type=kubernetes.io/basic-auth \
     --from-literal=username=kaneo --from-file=password="$RECOVERY_DIR/db-password"
   k apply -k kubernetes/databases/postgres
   k -n postgres wait --for=condition=Ready cluster/postgres --timeout=600s
   k -n postgres wait --for=jsonpath='{.status.applied}'=true \
     databaserole/kaneo database/kaneo --timeout=120s
   ```

3. **Import the dump** before Kaneo starts: the database must be empty, or
   Kaneo creates its own schema first.

   ```bash
   primary="$(k -n postgres get pod -o name -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary)"
   k -n postgres exec -i "$primary" -c postgres -- pg_restore --list < "$RECOVERY_DIR/kaneo.dump" | head -8
   k -n postgres exec -i "$primary" -c postgres -- \
     pg_restore -d kaneo --role=kaneo --no-owner --no-acl --exit-on-error < "$RECOVERY_DIR/kaneo.dump"
   ```

4. **Compare row counts** with the live database. Define `counts` on both
   machines, run `counts "$RESTORE_CONTEXT"` here and
   `counts "$PUBLIC_CONTEXT"` on the server, and compare. Only tables the live
   site changed since the capture may differ.

   ```bash
   counts() {
     local p q="select table_name, (xpath('/row/c/text()', query_to_xml(format(
       'select count(*) as c from public.%I', table_name), false, true, '')))[1]
       from information_schema.tables
       where table_schema = 'public' and table_type = 'BASE TABLE' order by 1"
     p="$(kubectl --context "$1" -n postgres get pod -o name \
       -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary)"
     kubectl --context "$1" -n postgres exec "$p" -c postgres -- psql -d kaneo -tAc "$q"
   }
   ```

5. **Start Kaneo** with the recovered credentials and a local address:

   ```bash
   k apply -f kubernetes/apps/kaneo/namespace.yaml
   k -n kaneo create secret generic kaneo-credentials --from-env-file="$RECOVERY_DIR/credentials.env"
   k -n kaneo create configmap kaneo-settings --from-literal=KANEO_CLIENT_URL=http://127.0.0.1:8912
   k apply -k kubernetes/apps/kaneo
   k -n kaneo rollout status deployment/kaneo --timeout=600s
   k -n kaneo port-forward --address=127.0.0.1 service/kaneo 8912:5173 &
   ```

6. **Use it** at `http://127.0.0.1:8912`:

   - [ ] Log in with your normal account.
   - [ ] Your workspaces, projects and tasks are all there.
   - [ ] Create a task.
   - [ ] Restart everything, then check the task is still there:

     ```bash
     kill %1
     k -n postgres delete pod -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary
     k -n postgres wait --for=condition=Ready cluster/postgres --timeout=300s
     k -n kaneo rollout restart deployment/kaneo
     k -n kaneo rollout status deployment/kaneo --timeout=300s
     k -n kaneo port-forward --address=127.0.0.1 service/kaneo 8912:5173 &
     ```

7. **Clean up:** `kill %1`, `k3d cluster delete kaneo-restore` and
   `rm "$KUBECONFIG"`. Keep the recovery set only on encrypted storage.

A successful import alone is not a passed drill; the app must work on it.
Kaneo regenerates its runtime files on startup, so there is no file archive.
If S3 uploads are added later, the object store must join the recovery set.

## Take it offline

To withdraw the public site, remove only its Caddy route (see above) and keep
its database, credentials and recovery sets. The private Compose Kaneo is a
separate instance: never restore its database over this one. Never delete a
PVC to roll back an image; retained PVCs don't survive disk failure or a K3s
uninstall.
