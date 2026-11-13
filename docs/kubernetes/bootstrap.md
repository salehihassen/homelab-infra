# Bootstrap the single-node public cluster

These are operator instructions, not evidence of a deployment. Run only on the
selected public VPS (`server-2`) and an operator workstation. The private
cluster is deferred. Nothing here migrates an existing Compose service.

## 1. Discover and prepare the public VPS

Record OS/init system, CPU architecture, spare CPU/RAM, free disk/inodes,
mounts, Docker networks/listeners, host/provider routes and backup access. Use
a supported systemd Linux host. Allow capacity for K3s, Flux, the workloads
enabled under `kubernetes/clusters/public-edge` and the existing Compose
workloads; resource requests are not proof of capacity. On a host shared with
Compose, size the `kubelet-arg` memory reservations in the example config from
measured Compose usage. Bring layers up one at a time (K3s, Flux, then
workloads), rechecking host memory and Compose health after each, and stop
before continuing if either degrades.

Record existing Compose health and public URLs before installation. Review the
Pod/Service CIDRs against all host, Docker, LAN/VPN and provider routes. Reserve
disjoint ranges for the future private cluster. Confirm IP forwarding and CNI
requirements; do not change Docker or the OS as part of a blind install.

Before installation, restrict API port 6443 to approved administrator access
using host/provider firewalls. Keep 6443, 10250 and 8472 inaccessible from the
public internet. node-ip and tls-san do not restrict the API listener. Preserve
SSH and existing Compose ingress. An existing admin VPN is acceptable for API
access; it does not select inter-node networking. Public agents will not use
Tailscale as their inter-node transport and are not enrolled in this change.

## 2. Install one K3s server

This branch pins K3s **v1.37.2+k3s1**, Flux **2.10.0**, and Flux Operator
**0.62.1**, including its Mission Control dashboard.
Check supported versions before a future installation; upgrades are separate
reviewed changes. K3s server also runs workloads, so no separate agent install
is needed. Use K3s containerd, not `--docker`.

1. Copy `provisioning/k3s/public-edge.yaml.example` to ignored
   `local/public-edge.yaml`. Replace every `REPLACE_WITH_...` marker. Select the
   node's address and a protected administrator API hostname/IP in tls-san;
   review routes and CIDRs. Do not assume a public IP is a safe admin endpoint.
2. Confirm there is no prior K3s installation. Privately transfer the reviewed
   config to the public VPS, then run the following **on that VPS**. The two
   existence checks must succeed; otherwise stop and inspect the installation.

   ```bash
   set -euo pipefail
   test ! -e /etc/systemd/system/k3s.service
   test ! -d /var/lib/rancher/k3s
   sudo install -d -m 0700 /etc/rancher/k3s
   sudo install -m 0600 /path/to/reviewed-config.yaml /etc/rancher/k3s/config.yaml
   if sudo grep -q REPLACE_WITH /etc/rancher/k3s/config.yaml; then
     echo 'Unresolved configuration markers; stop and review.' >&2
     exit 1
   fi
   curl -fL 'https://raw.githubusercontent.com/k3s-io/k3s/v1.37.2%2Bk3s1/install.sh' -o /tmp/k3s-install.sh
   less /tmp/k3s-install.sh
   sudo env INSTALL_K3S_VERSION='v1.37.2+k3s1' sh /tmp/k3s-install.sh server
   ```

   The installer verifies binary checksums. No installer is run by repository
   validation. Traefik/ServiceLB stay disabled; Caddy keeps ports 80/443.
3. Retrieve `/etc/rancher/k3s/k3s.yaml` securely into a mode-0600 file on the
   workstation. Set its API URL to the protected address covered by tls-san.
   Give the context a unique name such as `public-edge`. Never commit it.
4. On the **workstation**, export `KUBECONFIG` to that file and `PUBLIC_CONTEXT`
   to the reviewed context name. A context name alone does not choose a file.

   ```bash
   : "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}"
   export KUBECONFIG PUBLIC_CONTEXT
   kubectl --context "$PUBLIC_CONTEXT" cluster-info
   kubectl --context "$PUBLIC_CONTEXT" get nodes -o wide
   kubectl --context "$PUBLIC_CONTEXT" -n kube-system get pods
   kubectl --context "$PUBLIC_CONTEXT" get storageclass
   ```

Expect one Ready node, CoreDNS/local-path provisioner healthy and no Traefik,
ServiceLB or metrics-server. On the VPS, confirm the pod memory cap took effect
(`cat /sys/fs/cgroup/kubepods.slice/memory.max` or
`/sys/fs/cgroup/kubepods/memory.max`, depending on the cgroup driver) and that
node allocatable memory reflects the reservations. Recheck existing Compose
health/listeners and public routes. From an untrusted external machine verify
that the API is unreachable.

## 3. Install Flux Operator and prepare inputs

Live sync tracks protected main. Merge/review this change before enabling it.
For GitOps rehearsal before merge, use a separate disposable repository with
these files on its main branch. A local-only render cannot prove Git deploy-key
authentication or continuous reconciliation from GitHub.

Install Flux CLI 2.10.0 and provide these private workstation inputs:

- `KUBECONFIG`, `PUBLIC_CONTEXT`: the public cluster's file/context.
- `GIT_REPOSITORY_URL`: SSH clone URL, without an embedded token.
- `KEY_FILE`: public cluster's SSH private deploy key, mode 0600.
- `AGE_KEY_FILE`: public cluster's age private key, mode 0600.

Generate new SSH/age keys with ssh-keygen/age-keygen in private storage. Register
the SSH public key as a read-only repository deploy key. Verify the Git SSH host
fingerprint against GitHub's published fingerprints. Back up both private keys
independently. The later private cluster must use different identities/keys.
No GitHub PAT or Git write credential is placed in Kubernetes.

From the repo root on the workstation:

```bash
: "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}" "${GIT_REPOSITORY_URL:?}" "${KEY_FILE:?}" "${AGE_KEY_FILE:?}"
export GIT_REPOSITORY_URL KUBECONFIG
flux --context "$PUBLIC_CONTEXT" check --pre
kubectl --context "$PUBLIC_CONTEXT" apply --server-side --field-manager=kustomize-controller \
  -f kubernetes/infrastructure/flux-operator/namespace.yaml
kubectl --context "$PUBLIC_CONTEXT" apply --server-side --field-manager=kustomize-controller \
  -k kubernetes/infrastructure/flux-operator
kubectl --context "$PUBLIC_CONTEXT" wait --for=condition=Established --timeout=90s \
  crd/fluxinstances.fluxcd.controlplane.io crd/fluxreports.fluxcd.controlplane.io
kubectl --context "$PUBLIC_CONTEXT" -n flux-system rollout status deployment/flux-operator --timeout=180s
flux --context "$PUBLIC_CONTEXT" create secret git flux-system \
  --url="$GIT_REPOSITORY_URL" --private-key-file="$KEY_FILE"
kubectl --context "$PUBLIC_CONTEXT" -n flux-system create secret generic cluster-settings \
  --from-literal=GIT_REPOSITORY_URL="$GIT_REPOSITORY_URL"
kubectl --context "$PUBLIC_CONTEXT" -n flux-system create secret generic sops-age \
  --from-file=age.agekey="$AGE_KEY_FILE"
```

Stop here and prepare every workload enabled under
`kubernetes/clusters/public-edge`. Each workload's setup doc, linked from
[kubernetes/README.md](../../kubernetes/README.md), lists the inputs kept out
of Git: storage-owner node labels, Secrets, ConfigMaps and the recoverable
copies to save. Installing the operator alone does not install the Flux
controllers or start app sync. For existing Secrets, inspect ownership
privately; do not overwrite them to force bootstrap.

## 4. Start Git reconciliation

This step deploys every workload enabled in Git. Each Flux Kustomization's
`dependsOn` orders them, so apps wait for their databases to become Ready.
The FluxInstance now installs the two selected Flux controllers and creates
the GitRepository/root Kustomization. From the same workstation shell:

```bash
set -o pipefail
cat kubernetes/clusters/public-edge/flux-instance.yaml \
  | flux envsubst --strict \
  | kubectl --context "$PUBLIC_CONTEXT" apply --server-side --field-manager=kustomize-controller -f -
kubectl --context "$PUBLIC_CONTEXT" -n flux-system wait --for=condition=Ready \
  fluxinstance/flux --timeout=600s
kubectl --context "$PUBLIC_CONTEXT" -n flux-system rollout status deployment/source-controller --timeout=180s
kubectl --context "$PUBLIC_CONTEXT" -n flux-system rollout status deployment/kustomize-controller --timeout=180s
flux --context "$PUBLIC_CONTEXT" reconcile source git flux-system
flux --context "$PUBLIC_CONTEXT" reconcile kustomization flux-system --with-source
kubectl --context "$PUBLIC_CONTEXT" -n flux-system wait --for=condition=Ready \
  kustomizations.kustomize.toolkit.fluxcd.io --all --timeout=600s
flux --context "$PUBLIC_CONTEXT" get all
```

Cold image downloads can exceed a readiness timeout. Inspect Pod events and
operator conditions before retrying the wait; a slow pull is not a reason to
reinstall the operator or recreate credentials.

The operator owns the controllers and generated source/root sync. The root
owns the operator installation, FluxInstance, and database/app reconciliations.
It reads the Git URL from cluster-settings when applying the FluxInstance from
Git; missing substitution fails. No second source/root/controller definition
competes with the operator. Secrets/URL ConfigMap remain human-managed, outside
Git and root pruning. No placeholder credentials or public hostname is deployed.
This is a fresh bootstrap; do not apply it as an unreviewed migration of an
already running directly installed Flux cluster.

## 5. Open Mission Control privately

In a separate workstation terminal:

```bash
: "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}"
kubectl --context "$PUBLIC_CONTEXT" -n flux-system port-forward --address=127.0.0.1 \
  service/flux-operator 9080:9080
```

Open `http://127.0.0.1:9080`. For everyday tailnet access without port-forward,
see [dashboard access](gitops.md#dashboard-access-and-daily-use). The dashboard
is read-only and has no separate login in this initial configuration:
authorization happens when your kubeconfig opens the port-forward. The ClusterIP
Service has no public route. Its Pod ingress is denied by NetworkPolicy,
including from other namespaces; host-origin traffic and the Kubernetes
port-forward have different policy semantics and are not application
authentication. Do not add a public Caddy route or bind the port-forward to all
interfaces. SSO/RBAC is a later prerequisite for broader access.

Look for the public FluxInstance, source/root sync and each enabled database
and app. Follow their dependency/health views and inspect events/logs
locally. Missing databases or credentials should remain visible as failures,
not be hidden by dummy values. See [GitOps ownership](gitops.md) and follow
[the test walkthrough](testing.md).

## Backup, rollback and later agents

Before persistent use, add and rehearse backup of:

- K3s config, server token (`/var/lib/rancher/k3s/server/token`) and secrets
  encryption material. For SQLite, stop only K3s and cold-copy its complete
  `/var/lib/rancher/k3s/server` directory plus config; restart even if capture
  fails. Compose stays running. etcd-snapshot is not a SQLite backup method.
- Each stateful workload's recovery set, as its setup doc defines it: usually
  a native database export plus the matching credentials/settings from one
  recovery point.
- SSH/age keys, kubeconfig and recovery instructions in private recoverable
  storage. Existing Compose backup discovery does not capture these PVCs.

Restore K3s to an isolated matching-version server with original config/token,
then verify the application. Alternatively rebuild from Git and restore the
external inputs/native app recovery set to fresh PVCs. Retained disks from a
lost API are not an automatic restore. Define backup schedule/retention and
RPO/RTO before accepting valuable public writes.

For manual maintenance first annotate FluxInstance `flux` with
`fluxcd.controlplane.io/reconcile=disabled`, then pause root, app and database
reconcilers. Resume children/root and remove the annotation afterwards. This
prevents the operator from repairing the paused root while you work. Workload
backup procedures include their own cleanup for this pause. To retire a
service, disable only its Caddy route, stop its reconciliation, and preserve
its state/credentials. All existing Compose services remain independently owned.
K3s uninstall removes local-path data and alters host networking; there is no
blind uninstall command for a host shared with Compose.

Adding VPS/ARM agents is a later change: validate supported OS/version, routes,
firewalls, multi-cloud CNI, MTU, DNS, policy enforcement and ARM runtime. Do not
label a second node as storage owner for a workload pinned to one node. Extra agents do not make local data
available after this server fails. The architecture records the later serving
continuity goal without introducing control-plane HA now.

## References and controller updates

- K3s configuration: <https://docs.k3s.io/installation/configuration>
- K3s multicloud: <https://docs.k3s.io/networking/distributed-multicloud>
- K3s SQLite restore: <https://docs.k3s.io/datastore/backup-restore>
- Flux Operator install: <https://fluxoperator.dev/docs/installation/>
- FluxInstance sync: <https://fluxoperator.dev/docs/instance/sync/>
- Mission Control access: <https://fluxoperator.dev/docs/web-ui/user-management/>
- Flux SOPS: <https://fluxcd.io/flux/guides/mozilla-sops/>

Operator/chart updates are separate reviewed changes. Update the version,
verified chart checksum and image digest in the rendering script/values, then
regenerate the chart. Helm is only needed for regeneration; this installation
uses no Helm release and adds no Flux Helm controller:

```bash
bash scripts/render-flux-operator.sh
bash scripts/validate-kubernetes.sh
```

Flux upgrades change the exact `spec.distribution.version` in the FluxInstance.
Refresh the validation-only Flux CRD snapshot from that operator-supported
release and rehearse the generated manifests, patches, dashboard and app/database
reconciliation. Never edit operator-owned live controllers as an upgrade method.
