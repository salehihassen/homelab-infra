# Set up the public K3s cluster

Run everything on the public server (`server-2`), from the repo root, unless a
step says otherwise. A workstation also works if its kubeconfig reaches the API
through your admin VPN. Existing Compose services keep running throughout.

Pinned versions: K3s **v1.37.2+k3s1**, Flux **2.10.0**, Flux Operator
**0.62.1**. Upgrades are separate reviewed changes (see the end of this page).
The reasoning behind each choice is in [architecture.md](architecture.md) and
[gitops.md](gitops.md).

Paste each code block whole. A block pasted in pieces can run a `create`
command half-finished; if that happens, delete what it made and rerun it.

## 0. Before you start

- [ ] No earlier K3s: `test ! -d /var/lib/rancher/k3s` succeeds.
- [ ] At least 25% disk free (`df -h /`). K3s evicts every Pod when the disk
      runs low, and won't run them again until it frees more space.
- [ ] The host firewall blocks 6443, 10250 and 8472 from the internet. Setting
      `node-ip` or `tls-san` does not.
- [ ] The Pod and Service ranges in the config example don't overlap any route
      in `ip route` or `docker network inspect`.
- [ ] A baseline to compare against later:

```bash
free -m; docker ps --format '{{.Names}}\t{{.Status}}'; sudo ss -lntup > /tmp/ss-before.txt
```

## 1. Install K3s

```bash
umask 077; mkdir -p local
cp provisioning/k3s/public-edge.yaml.example local/public-edge.yaml
"${EDITOR:-nano}" local/public-edge.yaml   # replace every REPLACE_WITH_...
```

Size `system-reserved` from what Compose actually uses (`docker stats`). Then:

```bash
sudo install -d -m 0700 /etc/rancher/k3s
sudo install -m 0600 local/public-edge.yaml /etc/rancher/k3s/config.yaml
sudo grep -c REPLACE_WITH /etc/rancher/k3s/config.yaml   # must print 0
```

```bash
curl -fL 'https://raw.githubusercontent.com/k3s-io/k3s/v1.37.2%2Bk3s1/install.sh' -o /tmp/k3s-install.sh
less /tmp/k3s-install.sh                   # read it before running it
sudo env INSTALL_K3S_VERSION='v1.37.2+k3s1' sh /tmp/k3s-install.sh server
```

The node is named after `node-name` in the config (`public-server` in the
example), not after the host.

## 2. Tools and kubeconfig

**Flux CLI**, checksum-verified (`uname -m`: x86_64 → amd64, aarch64 → arm64):

```bash
cd /tmp
v=2.10.0; arch=amd64
curl -fLO "https://github.com/fluxcd/flux2/releases/download/v$v/flux_${v}_linux_${arch}.tar.gz"
curl -fLO "https://github.com/fluxcd/flux2/releases/download/v$v/flux_${v}_checksums.txt"
grep "flux_${v}_linux_${arch}.tar.gz" "flux_${v}_checksums.txt" | sha256sum --check --strict
tar -xzf "flux_${v}_linux_${arch}.tar.gz" flux && sudo install -m 0755 flux /usr/local/bin/flux
cd -
command -v age-keygen || sudo apt install -y age
```

**Optional: the official kubectl.** K3s's built-in `kubectl` warns
`config.yaml.d: permission denied` for non-root users. The warning is harmless.
To silence it, install the official one under another name; the K3s installer
owns `/usr/local/bin/kubectl`:

```bash
cd /tmp
v=v1.37.2
curl -fLO "https://dl.k8s.io/release/$v/bin/linux/amd64/kubectl"
curl -fLO "https://dl.k8s.io/release/$v/bin/linux/amd64/kubectl.sha256"
echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check --strict
sudo install -m 0755 kubectl /usr/local/bin/kubectl.upstream
cd -
```

**Kubeconfig** as your own user, so you don't need `sudo kubectl`:

```bash
umask 077; mkdir -p ~/.kube
sudo install -m 0600 -o "$USER" /etc/rancher/k3s/k3s.yaml ~/.kube/public-edge.yaml
cat >> ~/.bashrc <<'EOF'
alias kubectl=kubectl.upstream      # drop this line if you skipped the official kubectl
export KUBECONFIG=~/.kube/public-edge.yaml PUBLIC_CONTEXT=public-edge
EOF
source ~/.bashrc
kubectl config rename-context default public-edge
kubectl --context "$PUBLIC_CONTEXT" get nodes -o wide
kubectl --context "$PUBLIC_CONTEXT" -n kube-system get pods
```

Expect one Ready node, healthy CoreDNS and local-path, and no Traefik,
ServiceLB or metrics-server. Check `free -m` and `docker ps` against the
baseline.

## 3. Create Flux's keys

```bash
umask 077; mkdir -p local/public-edge-keys
ssh-keygen -t ed25519 -N '' -C public-edge-flux -f local/public-edge-keys/flux-deploy
age-keygen -o local/public-edge-keys/age.agekey
cat local/public-edge-keys/flux-deploy.pub
```

**Add the deploy key on GitHub now:** the repo's **Settings → Deploy keys →
Add deploy key**. Paste the `.pub` line and leave **Allow write access**
unchecked. Without it, Flux fails later with "Source artifact not found".

```bash
export KEY_FILE=local/public-edge-keys/flux-deploy AGE_KEY_FILE=local/public-edge-keys/age.agekey
export GIT_REPOSITORY_URL=ssh://git@github.com/<owner>/homelab-infra.git
```

Copy both private keys **off the server** (password manager or another
machine). A copy that only exists on the server is lost with the server.

## 4. Install Flux Operator and Flux's Secrets

This installs the operator only; nothing deploys yet.

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

**Check GitHub's host key.** This prints only public fingerprints; compare
them with GitHub's "SSH key fingerprints" page and stop if they differ:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n flux-system get secret flux-system \
  -o jsonpath='{.data.known_hosts}' | base64 -d | ssh-keygen -lf -
```

If a Secret already exists, find out who made it before replacing it.

## 5. Create the database and app inputs

Everything Flux deploys in step 6 needs these inputs, which stay out of Git:

1. **Pick the database node:** label exactly one node (use its name from
   step 2, `public-server` in the example):

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" label node public-server homelab.example/local-storage=true
   ```

2. **Each app's database password:** [postgres.md, Add an app](postgres.md#add-an-app),
   step 2.
3. **Each app's own inputs:** for Kaneo, [kaneo.md, Prepare](kaneo.md#prepare-the-new-instance).
4. **Copy `local/public-edge-inputs/` off the server**, with the keys.

## 6. Turn on GitOps

This deploys everything enabled under `kubernetes/clusters/public-edge`, in
dependency order. In a **second session**, watch:

```bash
flux --context public-edge get kustomizations --watch
```

In the first session:

```bash
: "${GIT_REPOSITORY_URL:?}" "${PUBLIC_CONTEXT:?}"
set -o pipefail
flux envsubst --strict < kubernetes/clusters/public-edge/flux-instance.yaml \
  | kubectl --context "$PUBLIC_CONTEXT" apply --server-side --field-manager=kustomize-controller -f -
kubectl --context "$PUBLIC_CONTEXT" -n flux-system wait --for=condition=Ready fluxinstance/flux --timeout=600s
flux --context "$PUBLIC_CONTEXT" reconcile kustomization flux-system --with-source
kubectl --context "$PUBLIC_CONTEXT" -n flux-system wait --for=condition=Ready \
  kustomizations.kustomize.toolkit.fluxcd.io --all --timeout=600s
```

**What the watch should show:**

1. `flux-system` Ready: Flux pulled `main` with the deploy key.
2. `cloudnative-pg`, then `postgres`. `postgres` stays not-ready for a minute
   or two while the database starts and the app roles are created.
3. Each app (`kaneo`) Ready last.

The first run pulls several images, so a slow first wait is normal. If a wait
times out, don't reinstall or recreate anything; see
[troubleshooting](testing.md#if-something-is-stuck). Then check `free -m` and
`docker ps` again.

## 7. Test it and open the dashboard

Work through [testing.md](testing.md). Mission Control, the read-only Flux
dashboard, is at `http://<server-2 Tailscale address>:9081` from your tailnet
once the [Caddy route](gitops.md#dashboard-access-and-daily-use) is set up.
Without it, use a port-forward:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n flux-system port-forward --address=127.0.0.1 \
  service/flux-operator 9080:9080
```

Then open `http://127.0.0.1:9080` (from a laptop, tunnel first:
`ssh -N -L 9080:127.0.0.1:9080 <server-2>`). Never bind it to all interfaces
or add a public route.

## Maintenance

**Pause Flux** before manual work, so it doesn't undo your changes. The
operator would otherwise repair the paused root:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n flux-system annotate fluxinstance/flux \
  fluxcd.controlplane.io/reconcile=disabled --overwrite
flux --context "$PUBLIC_CONTEXT" suspend kustomization flux-system
flux --context "$PUBLIC_CONTEXT" suspend kustomization <app-or-postgres>
# ... your work ...
flux --context "$PUBLIC_CONTEXT" resume kustomization <app-or-postgres>
flux --context "$PUBLIC_CONTEXT" resume kustomization flux-system
kubectl --context "$PUBLIC_CONTEXT" -n flux-system annotate fluxinstance/flux \
  fluxcd.controlplane.io/reconcile-
```

**Back up**, before valuable data goes in. Turn on the nightly K3s capture in
server-2's encrypted B2 backup
([backups.md](../backups.md#the-public-k3s-cluster-server-2)). It holds every
app's database dump, `local/public-edge-inputs/`, `local/public-edge-keys/` and
the kubeconfig.

It does not hold K3s's own state. To restore, rebuild from Git and restore each
app ([kaneo.md](kaneo.md#restore-drill)). To restore K3s itself instead, you
would also need `/etc/rancher/k3s/config.yaml` and the whole
`/var/lib/rancher/k3s/server` directory (token and secrets-encryption keys),
cold-copied with only K3s stopped, onto a server with the same K3s version.

**Retire an app:** remove only its Caddy route, stop its reconciliation, and
keep its database and credentials. **Never** delete a PVC to fix a startup
problem, and never blind-run the K3s uninstall script: it deletes local-path
data and changes host networking on a host shared with Compose.

**More nodes** are a later change. Adding agents needs a tested multi-cloud
network (not Tailscale), firewall and MTU checks, and does not keep the
database available if this server fails. See [roadmap.md](roadmap.md).

## Upgrades

- **Flux Operator:** update the version, chart checksum and image digest in
  `scripts/render-flux-operator.sh` and its values, then:

  ```bash
  bash scripts/render-flux-operator.sh
  bash scripts/validate-kubernetes.sh
  ```

- **Flux:** change `spec.distribution.version` in the FluxInstance and refresh
  the validation-only Flux CRD snapshot from that release.
- **K3s:** rerun the installer with the new `INSTALL_K3S_VERSION` after
  reading the release notes.

Merge the change, then watch it roll out. Never edit operator-owned
controllers directly.

References: [K3s configuration](https://docs.k3s.io/installation/configuration),
[K3s backup and restore](https://docs.k3s.io/datastore/backup-restore),
[Flux Operator](https://fluxoperator.dev/docs/installation/),
[FluxInstance sync](https://fluxoperator.dev/docs/instance/sync/),
[Mission Control access](https://fluxoperator.dev/docs/web-ui/user-management/),
[Flux SOPS](https://fluxcd.io/flux/guides/mozilla-sops/).
