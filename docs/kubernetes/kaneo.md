# Public Kaneo pilot: prepare, restore, cut over

Kaneo currently runs privately in Compose on server-1. This change adds a
**separate** public-cluster candidate and leaves that deployment intact.
Decide explicitly whether to migrate existing private Kaneo data into an
internet-facing application or start empty. Do not publish private project
content merely to prove the deployment path. Nothing here runs automatically.

## Prepare the public candidate

After the public cluster/Flux acceptance checks:

Set/export `KUBECONFIG` to the public cluster's own file and confirm
`PUBLIC_CONTEXT` matches it before running any of the commands below.

1. Choose and inspect the data disk on the initial public server. K3s's
   local-path provisioner uses its configured host storage (normally under
   `/var/lib/rancher/k3s/storage`). Set its path before allocation if needed.
   PVC sizes are requests; local-path does not enforce a disk quota. Check
   available space, ownership, inode usage and monitoring independently.
2. Label **only that node** for storage after confirming its cluster label:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" get nodes --show-labels
   kubectl --context "$PUBLIC_CONTEXT" label node "$PUBLIC_STORAGE_NODE" homelab.example/kaneo-storage=true
   ```

   Both deployments require this label and `homelab.example/cluster=public-edge`.
   PV node affinity and Recreate deployments prevent silent relocation or
   concurrent writers. This is one-node persistence, with expected downtime
   when that server is unavailable. It is not replicated storage or HA.
3. Create the namespace from `kubernetes/apps/kaneo/namespace.yaml`. Prepare a
   mode-0600 private env file with `POSTGRES_PASSWORD` and stable `AUTH_SECRET`,
   and a separate mode-0600 env file with `KANEO_CLIENT_URL` (the intended public
   HTTPS origin). For restoring an existing instance, preserve its auth secret
   and any other encryption keys/features used by that exact instance. The
   candidate database/user names in this pilot are both `kaneo`; check the
   source names/ownership and adapt a private restore plan if different.
4. Create inputs in the **public** cluster. Files live outside Git:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/apps/kaneo/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create secret generic kaneo-credentials \
     --from-env-file="$KANEO_CREDENTIALS_FILE"
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create configmap kaneo-settings \
     --from-env-file="$KANEO_SETTINGS_FILE"
   ```

   Both deployments reference the same password. Changing the Secret does not
   change the password inside an existing initialized PostgreSQL database;
   perform SQL rotation and restart the app as one coordinated operation.
   No SMTP, uploads/S3, Redis, OAuth or integrations are configured by this PR.
   If the source uses any, preserve and test their keys/data and policy rules
   before migrating. Do not silently omit them.
5. Back up the candidate credentials and configure its data-aware backup
   capture into the existing Restic/B2 system. Prove a restore to an isolated
   target before serving real writes. Kubernetes state is not yet covered by
   Compose's automatic backup declarations.
6. After these inputs and `sops-age` exist, make a reviewed activation change:
   change `clusters/public-edge/apps.yaml` to `suspend: false` and update the
   initial-pilot suspension assertion in `scripts/check-kubernetes.py` in that
   same change. Keep `prune: false`. Merge, reconcile, and verify the database
   and application are healthy. For a restoration, scale Kaneo to zero before
   importing a coherent source recovery set (procedure below). Keep Caddy/DNS
   unchanged until restoration and authentication are verified.

If adopting SOPS later, put encrypted runtime resources under the public app's
Kustomize path and add them to its resources. Encrypt both `data`/`stringData`
with only the **public** age recipient. Hostnames in ConfigMap values are also
private deployment inputs: encrypt them or keep them operator-managed. Plain
Kubernetes Secrets are base64, not encryption. The public app Kustomization
already references its public `sops-age` key. Add CI rules for any new encrypted
files before including them; never test with an actual private key in CI.

## Restore and validate before exposure

Use the existing Compose [Kaneo migration runbook](../../hosts/server-1/kaneo/MIGRATION.md)
to capture a native PostgreSQL recovery set and matching application files and
keys. A copied live PostgreSQL data directory is not suitable. Test with a copy;
at final cutover, stop the old writer and take one final consistent capture.
A rehearsal must use test users/content if source data is sensitive.

For operator-controlled restoration, prevent Flux from undoing the temporary
app scaling. Pause the parent too if it could reassert the child's spec:

```bash
flux --context "$PUBLIC_CONTEXT" suspend kustomization flux-system
flux --context "$PUBLIC_CONTEXT" suspend kustomization kaneo
kubectl --context "$PUBLIC_CONTEXT" -n kaneo scale deployment/kaneo --replicas=0
kubectl --context "$PUBLIC_CONTEXT" -n kaneo wait --for=delete pod \
  -l app.kubernetes.io/name=kaneo --timeout=120s
```

Keep PostgreSQL running and ready. For a rehearsal or database-only recovery
point in custom format:

```bash
# Produces a private export on the operator's machine. The app must be stopped
# while capturing its matching files; do not treat unrelated exports as one set.
umask 077
kubectl --context "$PUBLIC_CONTEXT" -n kaneo exec deployment/kaneo-postgres -- \
  pg_dump -U kaneo -d kaneo --format=custom > "$RECOVERY_DIR/kaneo.dump"
```

For the actual migration, adapt the source's native dump to the selected target
roles. The Compose backup can be cluster-wide SQL including roles: restore into
an isolated PostgreSQL 16 instance with a unique temporary bootstrap admin,
then export `kaneo` as a custom-format dump with the correct owner. Do not feed
cluster-wide SQL containing role/database creation blindly into the pilot's
already initialized database. Review `pg_restore --list` before importing.
For a **fresh, empty candidate only**, restore the database with:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n kaneo exec -i deployment/kaneo-postgres -- \
  pg_restore -U kaneo -d kaneo --no-owner --no-acl --exit-on-error < "$RECOVERY_DIR/kaneo.dump"
```

The target must be empty: Kaneo startup creates schema. If it has already run,
with the app still stopped, deliberately recreate only this candidate database
(`dropdb`/`createdb -O kaneo`) or restore to a fresh candidate. Do not apply that
operation to the source/production database. Preserve a recovery copy first.

Use the supplied temporary restricted helper for PVC `kaneo-data`. It is pinned
to the same storage owner, runs as UID/GID 1001, mounts only application data,
and is excluded from Flux. With the application still stopped:

```bash
kubectl --context "$PUBLIC_CONTEXT" apply -k kubernetes/operations/kaneo-data
kubectl --context "$PUBLIC_CONTEXT" -n kaneo wait --for=condition=Ready pod/kaneo-data-helper --timeout=120s
```

For capture, create this archive alongside the database dump:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n kaneo exec kaneo-data-helper -- \
  tar -C /recovery -cf - . > "$RECOVERY_DIR/kaneo-files.tar"
```

For restore instead, extract the matching source archive into an empty target:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n kaneo exec -i kaneo-data-helper -- \
  tar -C /recovery -xf - --no-same-owner < "$RECOVERY_DIR/kaneo-files.tar"
```

Remove the helper before starting the app:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n kaneo delete pod kaneo-data-helper
```

Capture and restore are alternative operations: never overwrite a source archive
by capturing an empty target. Review the archive's relative paths first. Files
must land at the PVC root, mounted at `/app/apps/api/data`; restored application
files belong to UID/GID 1001. Do not mount or edit a live database PVC. Keep the
app stopped until the database, files and stable keys from one recovery point
are all restored.

Resume the child and parent Flux reconcilers, verify their Ready status and
single-writer rollout, then use temporary administrative access:

```bash
flux --context "$PUBLIC_CONTEXT" resume kustomization kaneo
flux --context "$PUBLIC_CONTEXT" resume kustomization flux-system
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo-postgres
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo
kubectl --context "$PUBLIC_CONTEXT" -n kaneo port-forward --address=127.0.0.1 service/kaneo 8911:5173
```

For initial account setup, temporarily route the configured origin through a
trusted local/private proxy or SSH tunnel; the browser origin must match
`KANEO_CLIENT_URL`. Do not open public ingress with an unclaimed empty database.
First-account bootstrap can still register an admin despite
`DISABLE_REGISTRATION=true`. Claim it before exposure, verify unauthenticated
access fails, guest access is disabled, and subsequent registration is denied.
For restored data, verify the expected existing administrator instead. Check
projects, attachments/features in use, sessions, restart survival and native
backup/restore. Health checks alone do not prove an application migration.

## Opt-in Caddy cutover

The public host's existing Caddy uses host networking. For this first
single-server pilot, it can reach the Kaneo **ClusterIP** through that host's
K3s service rules. There is no NodePort, port-forward daemon or second TLS
listener. This assumes Caddy and the selected public storage node are the same
host; prove the path from the actual Caddy container before changing routing.

1. Read the actual Service IP with `kubectl --context "$PUBLIC_CONTEXT" -n kaneo
   get service kaneo`. Set private `KANEO_K3S_UPSTREAM=<ClusterIP>:5173` and
   `KANEO_PUBLIC_DOMAIN` in `/opt/caddy/routing.env`, which is already loaded by
   the public Caddy Compose service. Use a dedicated public hostname chosen by
   the operator; it need not reuse the old private hostname.
2. From the public host and its Caddy container, request that upstream's
   `/api/health`. K3s's policy engine permits traffic from the local node;
   default-deny policies do not isolate a pod from its own node. This host is
   an administrative trust boundary. Verify real packet behavior and restrict
   host access; do not add an allow-all ingress rule to resolve a failed test.
3. Review/append the opt-in block in
   `provisioning/k3s/caddy-kaneo.caddy.example` to the public Caddyfile. It is not
   imported by any active configuration in this initial PR. Validate the full
   Caddy config and existing routes with the repository tooling, arrange
   DNS/certificates explicitly, and reload only after authorized cutover.
   If adding env values, recreate **only** the Caddy service to load them;
   a reload by itself does not refresh its process environment. Keep other
   routes and ports unchanged.
4. Test HTTPS, login, denial of registration, CRUD and client behavior from an
   external client. Confirm no direct 5173/5432 or Kubernetes API listener is
   exposed publicly. From unrelated pods test denial of Kaneo/PostgreSQL access
   and Kaneo-to-private/LAN egress. Host/cross-cluster access restrictions need
   firewall/Tailscale tests too; NetworkPolicy is only part of the boundary.
5. Keep the previous private Compose instance stopped if migrating its data,
   with its final backup and env files retained. If this was a fresh, separate
   public instance, retain the existing private instance as separately owned.

Do not depend on a ClusterIP remaining unchanged after deleting/recreating its
Service/cluster. Update the private upstream and test it during recovery. A
future ingress-controller/worker design should replace this same-host bridge
in a separate change with explicit 80/443 ownership and policy rules.

## Backup cadence and rollback

Define RPO/RTO and schedule coherent native database + app-file captures with
failure alerts and retention before accepting public writes. While the app is
stopped, a restricted helper on the same node can stream a tar of `kaneo-data`
into the same protected staging directory as the native DB dump. Back up stable
keys/config at that recovery point. Restart/resume the app in cleanup even when
capture fails, but mark the capture failed and never upload it as complete.
Feed that staging directory into the host's existing encrypted Restic snapshot;
never stage plaintext in tracked source. Prove an isolated import/file restore.
No backup automation is silently installed by this PR.

Rollback before accepting public writes: remove the new Caddy block/DNS change,
resume the original Compose Kaneo if it was stopped, and preserve the candidate
for investigation. After public writes, decide which database is authoritative;
stop writes, export and reverse-restore if compatible. Repointing traffic at a
stale Compose database loses new work. Never delete PVCs to roll back an image.
Local-path PVs and protected PVCs survive Flux pruning, but not disk failure or
an unreviewed K3s uninstallation.
