# Public-only pilot validation record

Revised 2026-11-09 for ephemeral Kaneo storage and the PostgreSQL StatefulSet.
These are local rehearsal results, not deployment evidence for the real fleet.
No live credentials, host configuration, DNS or ingress changed.

## Current scope checks

- The single public root and Kaneo app path render with
  Kustomize 5.8.1. Kubeconform 0.8.0 reports zero invalid resources/errors against
  Kubernetes 1.37 and the exact vendored Flux custom-resource schemas. Generated
  CRD definitions are excluded from Kubeconform and accepted by the actual API.
- The contract checker enforces public-only provisioning/root/app scope,
  active Kaneo reconciliation after operator preparation, protected persistence,
  restricted runtime, health/resource settings and no public workload listeners.
  It also requires a PostgreSQL StatefulSet with one retained data claim
  template, a matching headless Service and only ephemeral Kaneo mounts.
- Runbook Bash examples pass syntax checks and relative documentation links
  resolve. Existing host Compose definitions are unchanged from the base branch.

## Current storage rehearsal

A separate disposable K3s v1.37.2+k3s1 server ran the revised manifests directly,
with generated inputs and no Flux controllers, agents, ingress controller or
host-exposed application/database ports.

- Kaneo started without an application PVC under the existing restricted
  runtime settings. PostgreSQL ran as `kaneo-postgres-0`, with the governing
  headless Service and exactly one Bound claim, `data-kaneo-postgres-0`.
- Replacing both Pods preserved a test database row and allowed the registered
  account to sign in again. Marker files in both Kaneo `emptyDir` mounts were
  absent after replacement, confirming that runtime files are disposable.
- Scaling PostgreSQL to zero and back, then deleting and recreating its
  StatefulSet, preserved the claim's UID and test data. After workloads were
  Ready again, account sign-in succeeded. These deliberate single-database
  outages are persistence checks, not availability tests.
- A native PostgreSQL dump imported into an isolated scratch database preserved
  the test row. Node-origin HTTP reached Kaneo's ClusterIP health endpoint.
  Full browser CRUD and a complete application restore drill remain operator
  acceptance steps.

The disposable cluster and its kubeconfig were removed after testing. Existing
local development workloads were left alone.

## Earlier Flux and policy rehearsal

Before the storage revision, a Docker-backed K3s v1.37.2+k3s1 server ran Flux
2.10.0 and the earlier Deployment-based manifests with generated disposable
inputs. It had no agents, ingress controller or host-exposed app/database port.
These Flux and policy tests were not repeated for the storage-only revision;
the separate storage rehearsal above tests the new workload layout directly.

- Flux fetched a snapshot of the public-only manifests from a disposable local
  Git HTTP fixture on main. Source, root and Kaneo Kustomizations all became
  Ready. Strict substitution from cluster-settings and root self-management
  worked with those manifests. This tests actual Git artifact fetch and
  reconciliation; it does not test production GitHub SSH authentication.
- Pausing the app reconciler, scaling Kaneo to zero, resuming and reconciling
  restored the Git-declared replica.
- An unrelated restricted probe was denied Kaneo ingress, allowed by a temporary
  probe-only policy, then denied again after removing it. Kaneo egress to a
  separately reachable controlled target was denied. Test policies/probes were
  removed afterwards.
- First-account registration with the initial loopback origin succeeded (HTTP
  200); a second registration was rejected (HTTP 403). This is an API smoke
  test; full browser login/CRUD/public HTTPS remain operator acceptance steps.

The earlier test cluster, Git fixture and disposable credentials were removed.

## Existing repository checks

Compose/dependency/network checks and all 83 Python tests pass, with one existing
skip, in an Ubuntu validation container using CI's pinned Compose release. The
public-host Compose contracts also pass. Direct execution on this NixOS
workstation encounters the existing tests' Debian path/shell assumptions
(`/bin/true` and shell diagnostic text); the Ubuntu run exercises them without
changing the source or weakening assertions. Staged secret/private-identifier
checks and the actual commit hooks run before the local commit.

The earlier foundation's full Caddy/Alloy/Prometheus/Loki/public-host validation
remains applicable to unchanged host files. No fleet configuration is inferred
from those checks.

## Operator acceptance still required

Real GitHub deploy-key/SSH trust, disk/capacity selection, host/provider firewall
denials, public DNS/TLS, full browser authentication/CRUD and scheduled coherent
backups with an isolated application restore are not claimed by these results.
Follow [bootstrap](bootstrap.md), [Kaneo setup/recovery](kaneo.md) and the
[test walkthrough](testing.md). No ARM runtime, cross-provider routing, private
cluster deployment or server-outage continuity is included in this change.
