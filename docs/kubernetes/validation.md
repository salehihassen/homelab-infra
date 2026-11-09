# Public-only pilot validation record

Revised 2026-11-09 after narrowing scope to a fresh public Kaneo instance.
These are local rehearsal results, not deployment evidence for the real fleet.
No live credentials, host configuration, DNS or ingress changed.

## Current scope checks

- The single public root, Kaneo app path and manual recovery helper render with
  Kustomize 5.8.1. Kubeconform 0.8.0 reports zero invalid resources/errors against
  Kubernetes 1.37 and the exact vendored Flux custom-resource schemas. Generated
  CRD definitions are excluded from Kubeconform and accepted by the actual API.
- The contract checker enforces public-only provisioning/root/app scope,
  active Kaneo reconciliation after operator preparation, protected persistence,
  restricted runtime, health/resource settings and no public workload listeners.
- Runbook Bash examples pass syntax checks and relative documentation links
  resolve. Existing host Compose definitions are unchanged from the base branch.

## Disposable single-node rehearsal

A Docker-backed K3s v1.37.2+k3s1 server ran Flux 2.10.0 and the current
Kaneo/PostgreSQL manifests with generated disposable inputs. It has no agents,
no ingress controller and no host-exposed application/database port.

- Flux fetched a snapshot of the public-only manifests from a disposable local
  Git HTTP fixture on main. Source, root and Kaneo Kustomizations all became
  Ready. Strict substitution from cluster-settings and root self-management
  worked with the current manifests. This tests actual Git artifact fetch and
  reconciliation; it does not test production GitHub SSH authentication.
- Kaneo and PostgreSQL started under the restricted non-root/read-only settings,
  with two bound retained local PVCs. Node-origin HTTP reached Kaneo's ClusterIP,
  exercising the proposed same-host Caddy upstream path.
- Pausing the app reconciler, scaling Kaneo to zero, resuming and reconciling
  restored the Git-declared replica. Restarting PostgreSQL and Kaneo preserved
  a test database row and a marker file on the app PVC.
- An unrelated restricted probe was denied Kaneo ingress, allowed by a temporary
  probe-only policy, then denied again after removing it. Kaneo egress to a
  separately reachable controlled target was denied. Test policies/probes were
  removed afterwards.
- First-account registration with the initial loopback origin succeeded (HTTP
  200); a second registration was rejected (HTTP 403). This is an API smoke
  test; full browser login/CRUD/public HTTPS remain operator acceptance steps.
- A native PostgreSQL export imported into an isolated scratch database and
  preserved the test row. The manual restricted app-data helper captured the
  PVC marker while Kaneo was stopped; app/root reconciliation resumed after
  helper removal. This is not a full application restore drill.

The test cluster, Git fixture and disposable credentials are removed after
validation. The pre-existing local development cluster is left alone.

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
