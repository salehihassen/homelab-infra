# Public K3s foundation: a new Kaneo instance

Scope revised 2026-11-10. No fleet host has been installed or changed by this branch.
This first change introduces only a public cluster and an independent, empty
Kaneo instance. All existing services, including private Compose Kaneo, retain
their current Compose deployments, routes, credentials and data.

The [migration roadmap](roadmap.md) preserves the decisions and acceptance
criteria from both supplied handoffs. Narrowing this MR does not discard the
future private cluster or the single-server public serving-continuity goal.

## Initial topology

| Component | Initial placement | State |
| --- | --- | --- |
| public-edge | server-2, existing public VPS | One K3s server, also running workloads; SQLite datastore |
| Flux Operator / Mission Control | public-edge | Pinned operator with embedded read-only UI; loopback port-forward access |
| Flux | public-edge | Operator-managed source/Kustomize controllers; read-only Git deploy identity |
| Kaneo | public server | One Deployment replica, new credentials and temporary runtime storage |
| PostgreSQL 18 | public server | One StatefulSet replica and a retained 10 GiB data claim |
| Public ingress | Existing Compose Caddy on server-2 | Optional new Kaneo route after local acceptance |

A K3s server already runs the agent components needed to host Pods. Do not
install a second agent service on this machine. No worker enrollment, provider
provisioning, private-cluster bootstrap or Compose service migration is included.

```mermaid
flowchart TD
  Operator[Flux Operator / private Mission Control UI] --> Flux[Flux / public-edge root]
  Git[Protected Git main branch] --> Flux
  Flux --> K[Kaneo: fresh instance]
  Flux --> DB[Dedicated PostgreSQL / retained local PVC]
  K --> DB
  Internet --> C[Existing Compose Caddy]
  C -. reviewed new route after tests .-> K
```

## Coexistence and networking

K3s uses its own containerd. Traefik and ServiceLB are disabled so the existing
Compose Caddy keeps ports 80/443. This change installs no ingress controller,
LoadBalancer, NodePort, hostPort or hostNetwork workload. Kaneo and PostgreSQL
have ClusterIP Services. First access uses an administrator's loopback-only
port-forward. A later operator step adds only the new Kaneo hostname to Caddy,
using the same-host ClusterIP path; it does not replace existing routes.

Discover host routes, Docker networks, LAN/VPN ranges and spare resources
before selecting Pod/Service CIDRs or installing anything. The API must be
reachable only through approved administrative access and firewalls; setting
node-ip or tls-san alone does not restrict its listener. An existing Tailscale
connection may provide workstation administration. Public inter-node networking
will not use Tailscale. No cross-node path is implemented in this single-node
step; choose and test a documented K3s multi-cloud option before adding agents.
Initial Flannel VXLAN is local to this one node; never expose UDP 8472 publicly.

## GitOps and bootstrap ordering

Flux Operator owns the Flux controllers and generated source/root sync. The
public root reconciles the pinned operator installation, FluxInstance,
PostgreSQL and Kaneo. Git URL, deploy key, age private key, database password,
auth secret and application
origin are external bootstrap inputs. Source artifacts include only the public
root, PostgreSQL, Kaneo and operator paths. Git credentials have no write permission.
Keep main protected; controllers use strict substitution and reject cross-namespace
references/remote bases. No image automation is installed.

The administrator installs Flux Operator, prepares the namespace, dedicated
Secrets, URL ConfigMap, storage-owner label and recovery copies, then applies
the FluxInstance to install controllers and generate Git sync. Kaneo is active
in Git: starting root reconciliation deploys it without a second activation
PR. Its Flux reconciliation depends on the independently
managed PostgreSQL reconciliation being Ready. Missing inputs are a bootstrap
error, not a reason to add placeholder secrets. A feature branch is validated locally; live bootstrap
tracks reviewed main. Rehearse against a separate disposable repository if main
does not yet contain these files.

Credentials initially remain operator-managed. Kaneo's reconciler supports
SOPS/age once encrypted resources are deliberately added to its path. Plain
Kubernetes Secrets are base64, not encryption. Protect the public age key and
server token outside Git and test their recovery. A future private cluster must
use separate identities, keys, tokens, kubeconfigs and backups.

Mission Control is embedded in the operator, uses its upstream read-only mode
and has no separate login in this pilot. Access is by loopback port-forward
authorized through the protected Kubernetes API. Its ClusterIP has no public
route, and direct Pod ingress is denied; generated Flux allowance policies are
narrowed so they do not reopen it. Host-origin traffic remains a separate
firewall concern. Shared/public dashboard access requires SSO/RBAC first. See
[GitOps ownership and maintenance](gitops.md). Adding the operator does not add
a Helm controller or change application/database manifests.

## Runtime, persistence and exposure

Kaneo uses a one-replica Deployment with Recreate rollouts and only `emptyDir`
runtime mounts. PostgreSQL uses a one-replica StatefulSet, a governing headless
Service and a retained claim template. Its stable Pod `kaneo-postgres-0` reuses
`data-kaneo-postgres-0` across replacement. This gives stable identity and
persistence, not PostgreSQL replication or database HA. Both workloads use
non-root restricted runtime settings, health probes and bounded resources.
Kaneo retains its rehearsed image; PostgreSQL is pinned to 18.6-alpine, both
using multiarchitecture digests. The database mount follows the official
PostgreSQL 18 layout. See [database ownership and upgrades](postgres.md).
This is a new instance, with no import or reuse of existing private Kaneo
content or secrets.

Both workloads require the public-cluster label and the kaneo-storage label.
Apply storage ownership to exactly one inspected node. The retained local-path
database claim with WaitForFirstConsumer binds data to that node. Adding an ARM
agent provides compute capacity; it does not replicate this data or move Kaneo
automatically.

Default-deny policies allow Kaneo only cluster DNS and its own PostgreSQL.
SMTP, OAuth, webhooks, object storage and other integrations are out of scope.
Claim the first administrator through local access before exposing the app;
DISABLE_REGISTRATION does not prevent first-account bootstrap. Public use also
requires tested authentication, a coherent backup/restore and reviewed DNS/TLS.
Existing Compose backup discovery does not automatically protect this database.

## Later changes

1. Private/home cluster with one simple pilot, independently secured.
2. Public agents after validating routing, firewall rules, MTU, NetworkPolicy
   enforcement and ARM runtime compatibility. No public worker joins the
   private cluster or receives private-cluster credentials.
3. Public application-serving continuity during a temporary server VPS outage:
   pre-existing app/ingress replicas on surviving agents, external failover and
   surviving data/runtime dependencies. Keep one control-plane server unless
   outage tests or new requirements justify HA. The current local Kaneo data
   and same-host Caddy do not satisfy that future goal.
4. Migrate other services individually, with hardware/storage ownership and
   restore tests. Keep Docker-managing apps in Compose until a supported path
   exists.

See [bootstrap](bootstrap.md), [new Kaneo setup and recovery](kaneo.md), and
the [public-cluster test walkthrough](testing.md). Fleet capacity, real routes,
DNS/TLS, RPO/RTO and backup scheduling remain operator inputs. This branch
does not claim public deployment or fault tolerance.
