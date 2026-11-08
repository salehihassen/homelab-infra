# Bootstrap two independent clusters

These are operator instructions, not evidence that a host has been changed.
Use the repository's neutral aliases to map machines privately. Do not run
installation, firewall, DNS or cutover commands against an assumed machine.
Everything except the K3s host install runs on an operator workstation.

## Discover and prepare

For each initial server, record OS/init system, CPU architecture, spare CPU/RAM,
free disk/inodes, mounts, existing Docker networks/listeners, host routes,
Tailscale address/interface, provider firewall and backup access. Start with a
supported systemd Linux installation. The public server must have enough spare
capacity for K3s, Flux, Kaneo, PostgreSQL and existing Compose services; manifest
requests/limits are starting budgets, not proof of adequate capacity.

Check actual LAN, Docker, Tailscale and provider routes before accepting the
example Pod/Service CIDRs. Keep both clusters' CIDRs disjoint even though this
phase introduces no inter-cluster routes. Confirm IP forwarding and kernel/CNI
requirements against the pinned K3s documentation. Do not change the host OS or
Docker service as part of a blind install.

Before installing, use the host/provider firewall and Tailscale policy to allow
6443 only from approved administrators and future same-cluster nodes. Deny
public access to 6443, 10250 and 8472. No cross-cluster node access is needed.
K3s API defaults to listening on all host interfaces: setting `node-ip` and
`tls-san` does **not** restrict that listener. Retain working SSH/Tailscale and
Compose ingress. No blanket firewall script is supplied because the existing
rules and machine access paths have not been inspected.

## Install the first server in each cluster

Pin K3s to **v1.37.2+k3s1** and Flux CLI to **2.10.0**, verified from upstream
release metadata when this change was prepared. Validate against supported
versions before installing in the future. The generated Flux controllers use
that same Flux release. Only source-controller and kustomize-controller are
needed for these plain manifests; Helm and image automation can be added later.

1. Copy the appropriate `provisioning/k3s/<cluster>.yaml.example` to ignored
   `local/<cluster>.yaml`. Replace every `REPLACE_WITH_...` marker and review
   CIDRs. Do not reuse one cluster's config/token for the other.
2. On the **selected server**, verify no K3s installation already exists.
   Install the reviewed config as `/etc/rancher/k3s/config.yaml`, mode 0600.
   Do not overwrite an existing cluster's config. K3s must use its own
   containerd; do not pass `--docker`.
3. Download and review the installer from the pinned K3s tag; run it with the
   explicit version. These commands belong on the server, not this workstation:

   ```bash
   set -euo pipefail
   # Stop here if a previous installation exists.
   test ! -e /etc/systemd/system/k3s.service
   test ! -d /var/lib/rancher/k3s
   # After privately transferring the reviewed config:
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

   Treat the first two `test` commands as mandatory checks, not an automated
   script: a failure means stop and inspect. The installer verifies downloaded
   binary checksums. No installer is executed by this repository's validation.
4. Securely retrieve `/etc/rancher/k3s/k3s.yaml` into a separate mode-0600
   kubeconfig for that cluster. Change its server URL to the reviewed Tailscale
   address in `tls-san`. Give each context a unique name, and use explicit
   `--context` (and the correct exported `KUBECONFIG`) for every command. A
   context name does not select a different kubeconfig file. Never copy
   public-cluster kubeconfigs or tokens onto private workers or vice versa.
5. Verify Ready status, `local-path` provisioner, CoreDNS and NetworkPolicy
   controller health. Confirm Compose services and existing 80/443 listeners
   are unchanged, Traefik/ServiceLB absent, and the API inaccessible from an
   untrusted external host. On an ordinary cluster-local shell:

   ```bash
   kubectl --context "$CONTEXT" get nodes -o wide
   kubectl --context "$CONTEXT" -n kube-system get pods
   kubectl --context "$CONTEXT" get storageclass
   ```

## Bootstrap Flux without Git write credentials in either cluster

Merge/review this foundation into the intended protected `main` before enabling
reconciliation. For a rehearsal use a separate disposable repository with its
own main branch, not an unreviewed live feature branch. The bootstrap below
only writes Kubernetes resources; it makes no Git commits, deploy keys or PRs.

Repeat independently for `private-home` and `public-edge`. Supply these local
shell variables from private configuration:

- `CLUSTER`: exactly `private-home` or `public-edge`.
- `CONTEXT`, `KUBECONFIG`: the matching cluster's reviewed context and file.
- `GIT_REPOSITORY_URL`: SSH clone URL of the repository, with no embedded token.
- `KEY_FILE`: that cluster's own SSH private deploy key file (mode 0600).
- `AGE_KEY_FILE`: that cluster's own age private key file (mode 0600).

Generate different SSH keys and age keys for each cluster into ignored/private
storage using `ssh-keygen` and `age-keygen`. Register each SSH public key on the
repository as a **read-only** deploy key; save keys in independently recoverable
private storage. Verify the Git SSH host fingerprint against GitHub's published
fingerprints before using it. No GitHub PAT is needed inside Kubernetes.

```bash
# Context and URL are required; do not rely on your current default context.
: "${CLUSTER:?}" "${CONTEXT:?}" "${KUBECONFIG:?}" "${GIT_REPOSITORY_URL:?}" "${KEY_FILE:?}" "${AGE_KEY_FILE:?}"
case "$CLUSTER" in private-home|public-edge) ;; *) exit 1 ;; esac
export GIT_REPOSITORY_URL KUBECONFIG
flux --context "$CONTEXT" check --pre

# Apply only the shared controller foundation first so CRDs/namespace exist.
kubectl --context "$CONTEXT" apply --server-side -k kubernetes/infrastructure/flux-system
kubectl --context "$CONTEXT" wait --for=condition=Established --timeout=90s \
  crd/gitrepositories.source.toolkit.fluxcd.io crd/kustomizations.kustomize.toolkit.fluxcd.io

# Identities are not tracked or pruned by the root Kustomization.
flux --context "$CONTEXT" create secret git flux-system \
  --url="$GIT_REPOSITORY_URL" --private-key-file="$KEY_FILE"
kubectl --context "$CONTEXT" -n flux-system create secret generic cluster-settings \
  --from-literal=GIT_REPOSITORY_URL="$GIT_REPOSITORY_URL"
kubectl --context "$CONTEXT" -n flux-system create secret generic sops-age \
  --from-file=age.agekey="$AGE_KEY_FILE"

# Reproduce Flux's strict substitution for its first, operator-applied sync.
# No private bootstrap output is written into the source tree.
set -o pipefail
cat "kubernetes/clusters/$CLUSTER/flux-system/gotk-sync.yaml" \
  | flux envsubst --strict \
  | kubectl --context "$CONTEXT" apply --server-side -f -
flux --context "$CONTEXT" reconcile source git flux-system
flux --context "$CONTEXT" reconcile kustomization flux-system --with-source
flux --context "$CONTEXT" get all
```

For an already-created Secret, inspect its ownership privately before replacing
it. Do not overwrite credentials to make a failed bootstrap pass. The root
reconciler subsequently obtains the Git URL from `cluster-settings` and manages
its own source/controllers. Missing substitution is an error. Sparse source
artifacts include only that cluster's root, pilot and shared controller paths.
SOPS uses only the local `sops-age` key. This reduces mistakes; Git access to the
same repository still exposes its ciphertext and is a shared trust dependency.
No image automation or cluster-held Git write key is installed.

## Pilot acceptance

On private-home:

```bash
flux --context "$PRIVATE_CONTEXT" get kustomizations
kubectl --context "$PRIVATE_CONTEXT" -n podinfo rollout status deployment/podinfo
kubectl --context "$PRIVATE_CONTEXT" -n podinfo port-forward --address=127.0.0.1 service/podinfo 9898:9898
# In another terminal: curl --fail http://127.0.0.1:9898/healthz
```

There is no LAN/public route. Port-forward is an authenticated administrator
path through the API, not proof that in-cluster ingress is allowed. Confirm
requests to Podinfo from an unrelated pod are denied. Test the selected policy
engine; policy YAML alone is not evidence of enforcement. Demonstrate drift
repair by changing a disposable Deployment annotation and reconciling Flux.

On public-edge, `flux get kustomizations` must show `kaneo` **suspended**, with
no Kaneo pods/PVCs created automatically. Follow [the Kaneo runbook](kaneo.md)
before activation. Verify neither cluster can reach the other's API via node
network policy/firewall grants. Cluster credential separation must also hold.

## Backup and recovery before persistent use

Keep the existing Restic/B2 pipeline. It currently discovers Compose data, not
Kubernetes PVCs or K3s state. Do not claim that the pilot PVCs are protected by
existing Compose backup declarations.

Add and rehearse a separate, private host backup plan for each server:

- K3s config, the **server token** (`/var/lib/rancher/k3s/server/token`),
  credentials and config needed to restore secrets encryption.
- For the initial SQLite datastore, stop only `k3s` and capture the complete
  `/var/lib/rancher/k3s/server` directory plus config as a coherent cold copy.
  Restart K3s after capture, even on a backup failure. Compose stays running.
  `k3s etcd-snapshot` is not the backup method for this SQLite topology.
- For Kaneo, capture native PostgreSQL exports plus quiesced application files
  and credentials from the **same recovery point**, as described in its runbook.
  A raw live local-path directory is not an application-consistent DB backup.
- Store SSH/age keys, kubeconfigs, server token, Restic credentials and recovery
  instructions in private recoverable storage. A public cluster backup account
  must have no delete/decrypt access to the private cluster's backups.

Restore to an isolated matching-version server with the original token/config,
then restore application data and verify auth, ownership and policy enforcement
before routing traffic. If rebuilding the cluster instead, bootstrap from Git,
recreate external Secrets/ConfigMaps, allocate fresh PVCs and import the native
application recovery set. Retain PVs from a lost API are not automatic recovery.

## Rollback and later workers

Suspend app/root reconcilers before manual rollback so Flux does not fight it.
Rollback one app by reverting its reviewed Git change; validate schema/data
compatibility before downgrading Kaneo. Keep Compose's original Kaneo database
and routing as a recovery point; post-cutover writes require reverse migration.

To retire a pilot, first stop its Flux reconciliation and back up its state.
Delete only the selected pilot's objects. Kaneo namespace/PVCs and its storage
class have prune protection; retained PVs/data require deliberate recovery or
cleanup. There is no automated uninstall command here. K3s uninstall removes
cluster/local-path data and changes host networking: inspect the generated
uninstaller and verify backups and Compose traffic on a disposable machine
before considering it on a cohabiting host.

Before adding any VPS/ARM agent, confirm ownership, capacity, supported OS,
matching K3s version, disjoint routes, only same-cluster access and a tested CNI.
Test cross-node DNS, pod/Service reachability, policy denial, MTU, tunnel loss
and recovery; do not expose VXLAN on the internet. Check every image on ARM,
including Flux/system images. Do not label the ARM node as Kaneo's storage owner.
Do not provision a provider account or assume free capacity from this plan.

## Upstream references and controller updates

- K3s configuration: <https://docs.k3s.io/installation/configuration>
- K3s networking: <https://docs.k3s.io/networking/distributed-multicloud>
- K3s SQLite restore: <https://docs.k3s.io/datastore/backup-restore>
- Flux bootstrap: <https://fluxcd.io/flux/installation/bootstrap/github/>
- Flux SOPS: <https://fluxcd.io/flux/guides/mozilla-sops/>

To update the vendored controllers, use a checksum-verified Flux CLI of the
chosen release and regenerate in a reviewed dependency PR:

```bash
flux install --version=v2.10.0 --components=source-controller,kustomize-controller --export \
  > kubernetes/infrastructure/flux-system/gotk-components.yaml
bash scripts/validate-kubernetes.sh
```

Do not edit the generated CRDs by hand. Review the shared Kustomize patches,
release compatibility and both cluster roots on every upgrade. Renovate tracks
workload manifests; the Flux bundle/CLI/K3s version changes remain explicit.
