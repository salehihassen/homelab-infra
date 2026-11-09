# Bootstrap the single-node public cluster

These are operator instructions, not evidence of a deployment. Run only on the
selected public VPS (`server-2`) and an operator workstation. The private
cluster is deferred. Nothing here migrates an existing Compose service.

## 1. Discover and prepare the public VPS

Record OS/init system, CPU architecture, spare CPU/RAM, free disk/inodes,
mounts, Docker networks/listeners, host/provider routes and backup access. Use
a supported systemd Linux host. Allow capacity for K3s, Flux, Kaneo, PostgreSQL
and the existing Compose workloads; resource requests are not proof of capacity.

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

This branch retains the rehearsed K3s **v1.37.2+k3s1** and Flux **2.10.0** pins.
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

Expect one Ready node, CoreDNS/local-path provisioner healthy and no Traefik or
ServiceLB. Recheck existing Compose health/listeners and public routes. From an
untrusted external machine verify that the API is unreachable.

## 3. Install Flux controllers and prepare inputs

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
kubectl --context "$PUBLIC_CONTEXT" apply --server-side -k kubernetes/infrastructure/flux-system
kubectl --context "$PUBLIC_CONTEXT" wait --for=condition=Established --timeout=90s \
  crd/gitrepositories.source.toolkit.fluxcd.io crd/kustomizations.kustomize.toolkit.fluxcd.io
kubectl --context "$PUBLIC_CONTEXT" -n flux-system rollout status deployment/source-controller --timeout=180s
kubectl --context "$PUBLIC_CONTEXT" -n flux-system rollout status deployment/kustomize-controller --timeout=180s
flux --context "$PUBLIC_CONTEXT" create secret git flux-system \
  --url="$GIT_REPOSITORY_URL" --private-key-file="$KEY_FILE"
kubectl --context "$PUBLIC_CONTEXT" -n flux-system create secret generic cluster-settings \
  --from-literal=GIT_REPOSITORY_URL="$GIT_REPOSITORY_URL"
kubectl --context "$PUBLIC_CONTEXT" -n flux-system create secret generic sops-age \
  --from-file=age.agekey="$AGE_KEY_FILE"
```

Stop here and complete **Prepare the new instance** in [kaneo.md](kaneo.md):
select the one storage owner, create the dedicated credentials and local-origin
ConfigMap, and save recoverable copies. No app sync exists yet. For existing
Secrets, inspect ownership privately; do not overwrite them to force bootstrap.

## 4. Start Git reconciliation

Kaneo is enabled in Git. This step creates the app once its inputs are ready.
Controllers alone did not deploy it. From the same workstation shell:

```bash
set -o pipefail
cat kubernetes/clusters/public-edge/flux-system/gotk-sync.yaml \
  | flux envsubst --strict \
  | kubectl --context "$PUBLIC_CONTEXT" apply --server-side -f -
flux --context "$PUBLIC_CONTEXT" reconcile source git flux-system
flux --context "$PUBLIC_CONTEXT" reconcile kustomization flux-system --with-source
flux --context "$PUBLIC_CONTEXT" reconcile kustomization kaneo --with-source
flux --context "$PUBLIC_CONTEXT" get all
```

The root subsequently reads the Git URL from cluster-settings and manages its
own source/controllers. Missing substitution fails. Secrets/URL ConfigMap are
operator-managed, outside Git and root pruning. No placeholder credentials or
public hostname is deployed. Now follow [the test walkthrough](testing.md).

## Backup, rollback and later agents

Before persistent use, add and rehearse backup of:

- K3s config, server token (`/var/lib/rancher/k3s/server/token`) and secrets
  encryption material. For SQLite, stop only K3s and cold-copy its complete
  `/var/lib/rancher/k3s/server` directory plus config; restart even if capture
  fails. Compose stays running. etcd-snapshot is not a SQLite backup method.
- Native PostgreSQL export, quiesced Kaneo application files and stable keys
  from one recovery point; see [Kaneo recovery](kaneo.md).
- SSH/age keys, kubeconfig and recovery instructions in private recoverable
  storage. Existing Compose backup discovery does not capture these PVCs.

Restore K3s to an isolated matching-version server with original config/token,
then verify the application. Alternatively rebuild from Git and restore the
external inputs/native app recovery set to fresh PVCs. Retained disks from a
lost API are not an automatic restore. Define backup schedule/retention and
RPO/RTO before accepting valuable public writes.

For manual maintenance pause both root and app reconcilers. To retire this new
service, disable only its new Caddy route, stop its reconciliation, and preserve
its state/credentials. All existing Compose services remain independently owned.
Do not use an old private Kaneo database as rollback for this fresh instance.
K3s uninstall removes local-path data and alters host networking; there is no
blind uninstall command for a host shared with Compose.

Adding VPS/ARM agents is a later change: validate supported OS/version, routes,
firewalls, multi-cloud CNI, MTU, DNS, policy enforcement and ARM runtime. Do not
label a second node as Kaneo's storage owner. Extra agents do not make local data
available after this server fails. The architecture records the later serving
continuity goal without introducing control-plane HA now.

## References and controller updates

- K3s configuration: <https://docs.k3s.io/installation/configuration>
- K3s multicloud: <https://docs.k3s.io/networking/distributed-multicloud>
- K3s SQLite restore: <https://docs.k3s.io/datastore/backup-restore>
- Flux bootstrap: <https://fluxcd.io/flux/installation/bootstrap/github/>
- Flux SOPS: <https://fluxcd.io/flux/guides/mozilla-sops/>

Controller updates use a checksum-verified Flux CLI in a separate dependency
change; do not hand-edit generated CRDs:

```bash
flux install --version=v2.10.0 --components=source-controller,kustomize-controller --export \
  > kubernetes/infrastructure/flux-system/gotk-components.yaml
bash scripts/validate-kubernetes.sh
```
