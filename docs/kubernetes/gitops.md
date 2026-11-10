# Flux Operator and Mission Control

GitOps keeps the desired deployment in Git and repeatedly reconciles the
cluster to it. The Flux CLI and dashboard help inspect that process; normal
configuration changes still go through reviewed Git commits.

## Ownership and bootstrap

The initial public cluster runs three controller Deployments: Flux Operator
0.62.1, source-controller and kustomize-controller from Flux 2.10.0. Mission
Control is embedded in the operator process. No Flux Helm, notification, image
automation or Argo controller is added by this installation.

1. A workstation creates the flux-system namespace using its separate
   bootstrap manifest, applies the pinned operator installation from
   `kubernetes/infrastructure/flux-operator` and prepares the external inputs.
2. It applies `clusters/public-edge/flux-instance.yaml` after strictly
   substituting the private Git URL. The operator installs the selected Flux
   controllers and generates GitRepository/Kustomization `flux-system`.
3. That root follows protected main and manages the operator installation,
   FluxInstance and two child Kustomizations. PostgreSQL reconciles from
   `databases/postgres`; Kaneo reconciles from `apps/kaneo` only after postgres
   is Ready. The root substitutes the URL from the external cluster-settings
   Secret on every apply of the FluxInstance.

Only the operator manages the generated source/root and Flux controllers. The
root's inventory must not contain those resources. The operator and root must
not fight over their field ownership. The FluxInstance, operator CRDs and
namespace are protected from automatic pruning; retiring the installation is
an explicit maintenance operation. Database/app pruning remains disabled.
The namespace bootstrap manifest is intentionally outside the root Kustomize
resources: Flux Operator subsequently owns the generated namespace as well.

The operator/chart is vendored as rendered YAML so initial and subsequent
Kustomize applies use the same resources and require no Helm release or Helm
controller. `values.yaml` and `scripts/render-flux-operator.sh` record the
render inputs, pinned image index and verified chart archive checksum. Rerun
the script to update the generated `operator.yaml`. Validation-only Flux CRDs
in `kubernetes/schemas` are never applied; live CRDs belong to the operator.

This replaces a proposed direct-install bootstrap that has never run on the
fleet. A live Flux adoption/migration would require its own inventory/pruning
review before removing old controller definitions.

## Dashboard access and daily use

Use the loopback-only port-forward in [bootstrap](bootstrap.md), then open
`http://127.0.0.1:9080`. The upstream default UI provides read-only inspection:
resource health, dependencies, rollout details, events and Pod logs. It does
not display Secret/ConfigMap contents or permit reconcile/suspend/restart
actions in this mode. Use the CLI for those operations and keep changes in Git.

The UI has no separate login. The kubeconfig authorizes starting port-forward;
the default UI endpoint itself is anonymous. Its Service is ClusterIP with no
Ingress, NodePort or Caddy route. A deny-ingress policy selects the operator,
and patches narrow Flux's additive namespace policies to its two controllers
so they cannot accidentally allow dashboard access from other Pods. Host/node
traffic is not protected by Pod ingress policy in the same way; private API
access and host firewalls remain required. SSO/RBAC must precede shared or
external dashboard access. This dashboard is an administrator tool, not a new
internet-facing application.

For a deployment, check the Git revision, postgres readiness and Kaneo readiness.
Follow the database dependency when Kaneo is waiting; inspect events for missing
credentials, scheduling/storage failures and image pulls. Logs may contain
private information; do not publish screenshots or raw logs without review.

## SOPS and maintenance

The database/app Kustomizations still support SOPS/age with the separate
`sops-age` Secret. No credential was moved into Git by adding the operator.
The deploy key, age private key, URL setting and initial app credentials remain
external bootstrap/recovery inputs. A later deliberate change can introduce
encrypted Secret manifests; keep private decryption keys outside Git. The
operator does not replace SOPS or require another secrets service.

Before maintenance, pause FluxInstance reconciliation, then root and children;
otherwise the operator may restore the root's Git-declared settings. Resume
children/root and remove the FluxInstance pause annotation afterwards. See
[Kaneo backup](kaneo.md) for a cleanup-protected example.

The future private cluster uses its own operator, FluxInstance, kubeconfig,
read-only Git identity, encryption key and backups. No public operator receives
private-cluster credentials. Neither Flux nor this dashboard supplies application
HA, PostgreSQL replication or surviving ingress during a server outage; the
[roadmap](roadmap.md) preserves that later serving-continuity work.

## Upstream references

- Operator/chart: <https://fluxoperator.dev/docs/charts/flux-operator/>
- FluxInstance: <https://fluxoperator.dev/docs/crd/fluxinstance/>
- Sync ownership: <https://fluxoperator.dev/docs/instance/sync/>
- Dashboard access: <https://fluxoperator.dev/docs/web-ui/user-management/>
- SOPS with Flux: <https://fluxcd.io/flux/guides/mozilla-sops/>
