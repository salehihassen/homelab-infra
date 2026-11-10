# Test the new public K3s cluster

Use this walkthrough after [bootstrap](bootstrap.md) and
[Kaneo input preparation](kaneo.md). It tests the new public instance only.
Existing Compose services continue running; private-cluster work is deferred.
No VPS access is assumed and no commands below have been run on the real fleet.

GitOps means Git holds the intended manifests and Flux repeatedly applies them.
Here Flux tracks main: local edits or an unpushed feature branch do not change
the live cluster. First merge the reviewed files, or use a separate disposable
repository/cluster for rehearsal. Keep test content until the restore drill
passes. Commands run from the repo root on the workstation unless noted.

## 1. Validate files without deploying

With Python 3 and PyYAML available (activate your validation venv if needed):

```bash
bash scripts/validate-kubernetes.sh
```

Expect schema validation with zero invalid resources/errors and the public-only
scope/runtime/storage contract success message. This checks files, not the VPS,
Git authentication, live networking or backup recovery.

## 2. Check the intended cluster and existing services

Export KUBECONFIG to the reviewed public kubeconfig file and PUBLIC_CONTEXT to
its context name. Do not use whichever context happens to be your default.

```bash
: "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}"
export KUBECONFIG PUBLIC_CONTEXT
kubectl --context "$PUBLIC_CONTEXT" cluster-info
kubectl --context "$PUBLIC_CONTEXT" get nodes -o wide
kubectl --context "$PUBLIC_CONTEXT" -n kube-system get pods
kubectl --context "$PUBLIC_CONTEXT" get nodes -l homelab.example/kaneo-storage=true
```

Expect exactly one Ready public node, with that same node selected as storage
owner. CoreDNS/local-path should be healthy, Traefik/ServiceLB absent. On the
public VPS, compare `docker ps` and `sudo ss -lntup` with the pre-install baseline.
Open every existing public service URL and verify normal behavior. The public
server is also the only worker; a second local agent install is unnecessary.

## 3. Prove Git-to-cluster reconciliation

```bash
flux --context "$PUBLIC_CONTEXT" check
flux --context "$PUBLIC_CONTEXT" reconcile source git flux-system
flux --context "$PUBLIC_CONTEXT" reconcile kustomization flux-system --with-source
flux --context "$PUBLIC_CONTEXT" reconcile kustomization postgres --with-source
flux --context "$PUBLIC_CONTEXT" reconcile kustomization kaneo --with-source
flux --context "$PUBLIC_CONTEXT" get sources git
flux --context "$PUBLIC_CONTEXT" get kustomizations
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status statefulset/kaneo-postgres --timeout=300s
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
kubectl --context "$PUBLIC_CONTEXT" -n kaneo get deployment,statefulset,pods,svc,pvc -o wide
kubectl --context "$PUBLIC_CONTEXT" -n kaneo exec statefulset/kaneo-postgres -- \
  psql -U kaneo -d kaneo -tAc 'SHOW server_version; SHOW data_directory;'
```

Expect the source and all three Kustomizations (root, postgres and kaneo) Ready,
not suspended. Kaneo must depend on postgres being Ready. The source revision
must match the intended main commit. Expect one healthy Kaneo Pod, one healthy
PostgreSQL Pod named `kaneo-postgres-0`, two regular ClusterIP Services, one
headless governing Service and one Bound PVC named `data-kaneo-postgres-0`.
The SQL check must report PostgreSQL 18.6 and `/var/lib/postgresql/18/docker`.
Kaneo has only temporary runtime mounts and no application PVC. No other
application is reconciled. Pending Pods generally mean missing storage labels,
capacity or PVC provisioning; CreateContainerConfigError often means missing
input keys.

## 4. Open Kaneo locally and claim the administrator

The initial ConfigMap origin must be `http://127.0.0.1:8911`. In one terminal:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n kaneo port-forward --address=127.0.0.1 service/kaneo 8911:5173
```

In a second terminal:

```bash
curl --fail --show-error http://127.0.0.1:8911/api/health
```

Open `http://127.0.0.1:8911` in your browser, claim the first administrator and
create a test project/task. Log out and verify project data is inaccessible
without authentication. Verify a second registration is refused and guest
access is disabled. First-account bootstrap is possible even with
DISABLE_REGISTRATION=true, so claim it before exposing the app.

Port-forward is temporary authenticated admin access through Kubernetes. It
proves the app responds, not that public ingress or NetworkPolicy works. Keep
Caddy/DNS unchanged during this test. If login fails, verify browser origin
matches KANEO_CLIENT_URL exactly; localhost and 127.0.0.1 are different origins.

## 5. Prove Flux repairs drift

This deliberately stops only the disposable new app for a moment. Keep the
root reconciler active; pause the child, change its live replica count, then
resume it. Git still says one replica.

```bash
flux --context "$PUBLIC_CONTEXT" suspend kustomization kaneo
kubectl --context "$PUBLIC_CONTEXT" -n kaneo scale deployment/kaneo --replicas=0
flux --context "$PUBLIC_CONTEXT" resume kustomization kaneo
flux --context "$PUBLIC_CONTEXT" reconcile kustomization kaneo --with-source
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
kubectl --context "$PUBLIC_CONTEXT" -n kaneo get deployment kaneo
```

Expect one desired/ready replica again. The active root may restore the child
sooner; that is also Git reconciliation. Restart the port-forward if its Pod
changed, log in again and verify the test project/task remain. For permanent
changes, edit Git and merge; manual scaling is repaired back to Git's state.

## 6. Prove persistence and policy boundaries

Before real use, restart the PostgreSQL StatefulSet and Kaneo Deployment one at
a time:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout restart statefulset/kaneo-postgres
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status statefulset/kaneo-postgres --timeout=300s
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout restart deployment/kaneo
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
kubectl --context "$PUBLIC_CONTEXT" -n kaneo get pvc
kubectl --context "$PUBLIC_CONTEXT" -n kaneo get networkpolicy
```

Expect the same Bound database PVC, the stable `kaneo-postgres-0` Pod name,
successful login and the same test project/task. Verify that the replacement
Pod has a new UID but still mounts `data-kaneo-postgres-0`; Kaneo runtime files
are temporary and should not be treated as persistent data.
Restart the port-forward after Pod replacement. A restart proves local
persistence; it does not prove restore from backup or tolerance of VPS loss.

Verify policy enforcement with a temporary unrelated probe Pod: access to
Kaneo:5173 and PostgreSQL:5432 must fail. Prove the probe itself works against a
known reachable test target before accepting those denials. In an isolated
rehearsal, temporarily allow only that probe to Kaneo, confirm success, then
remove the test allowance and confirm denial again. Do not add a permanent
allow-all policy to pass this test. Kaneo's authenticated CRUD already exercises
its allowed database path. Also test denied Kaneo egress toward an otherwise
reachable controlled target; a failed DNS lookup alone is not an egress test.

Before the Caddy route, separately prove that the VPS and its host-networked
Caddy container can reach the actual Kaneo ClusterIP's `/api/health`. For the
current public Caddy container, on the VPS with KANEO_K3S_UPSTREAM set privately:

```bash
curl --fail --show-error "http://$KANEO_K3S_UPSTREAM/api/health"
docker exec caddy wget -qO- "http://$KANEO_K3S_UPSTREAM/api/health"
```

Port-forward does not test this path. From an untrusted external host, confirm
there is no public access to K3s 6443/10250/8472 or database/app 5432/5173. Host
firewalls and administrative VPN rules need separate checks from Pod policies.

## 7. Prove backup/restore, then public HTTPS

Follow the capture/isolated-restore procedure in [kaneo.md](kaneo.md), using the
test project/task. Verify the restored app and data through local access. Save
recovery keys independently; configure backup scheduling/failure alerts before
valuable writes. A successful pg_dump or merely seeing retained PVCs is not a
complete restore drill.

Then follow **Add only the new public HTTPS route** in kaneo.md. That changes
the app's configured origin and adds one site to existing Compose Caddy. From
an external client, with KANEO_PUBLIC_ORIGIN set to the chosen HTTPS origin:

```bash
curl --fail --show-error "$KANEO_PUBLIC_ORIGIN/api/health"
```

Verify a valid certificate, login, project/task create/edit/delete, denial of
unauthenticated project access, and denial of additional registration. Check
all old public URLs again and ensure no direct app/database/API ports opened.
If the new route fails, remove only that route and troubleshoot through local
access; leave existing Compose stacks and this instance's data intact.

## 8. Record the result and troubleshoot

Record the Git revision, versions, node/disk selection, acceptance results and
recovery location privately. Do not commit real domains, addresses or credentials.
Useful diagnostics that avoid printing Secret values:

```bash
flux --context "$PUBLIC_CONTEXT" get all
kubectl --context "$PUBLIC_CONTEXT" -n kaneo get events --sort-by=.lastTimestamp
kubectl --context "$PUBLIC_CONTEXT" -n kaneo describe pods
kubectl --context "$PUBLIC_CONTEXT" -n kaneo logs deployment/kaneo --tail=100
kubectl --context "$PUBLIC_CONTEXT" -n kaneo logs statefulset/kaneo-postgres --tail=100
```

Inspect logs locally and redact them before sharing; application logs and event
messages can contain private deployment details. Missing Git credentials/SSH
trust affect source readiness; wrong image permissions, storage mounts or
missing Secrets affect Pods. Do not delete PVCs to fix a startup failure.

Acceptance means this one-node public pilot works and existing Compose services
still work. Losing its sole VPS still means downtime. Agents, external failover,
surviving database/storage dependencies and an actual power-off test belong to
phase 2; do not use that future test as an availability claim for this pilot.
