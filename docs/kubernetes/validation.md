# Public-only pilot validation record

Revised 2026-11-10 for Flux Operator, Mission Control and database-first Flux.
These are local rehearsal results, not deployment evidence for the real fleet.
No live credentials, host configuration, DNS or ingress changed.

## Current scope checks

- The single public root, PostgreSQL database path and Kaneo app path render with
  Kustomize 5.8.1. Kubeconform 0.8.0 reports zero invalid resources/errors against
  Kubernetes 1.37 and the exact vendored Flux custom-resource schemas. Generated
  CRD definitions are excluded from Kubeconform and accepted by the actual API.
- The contract checker enforces public-only provisioning/root/app scope,
  active database-first reconciliation after operator preparation, protected persistence,
  restricted runtime, health/resource settings and no public workload listeners.
  It also requires a PostgreSQL StatefulSet with one retained data claim
  template, a matching headless Service, PostgreSQL 18 data layout, separate
  Flux inventories and only ephemeral Kaneo mounts.
- Operator and public-root renders are validated against pinned operator and
  Flux schemas. Contracts require the exact two selected Flux controllers,
  operator-only ownership of the source/root/controllers/namespace, retained
  SOPS references, internal Services and read-only dashboard configuration.
- Runbook Bash examples pass syntax checks and relative documentation links
  resolve. Existing host Compose definitions are unchanged from the base branch.

## Current Flux Operator and Mission Control rehearsal

A disposable Docker-backed K3s v1.37.2+k3s1 server ran Flux Operator 0.62.1
with its embedded UI and Flux 2.10.0 source/Kustomize controllers. A separate
local Git HTTP fixture served main; no GitHub or fleet credentials were used.
The operator image is pinned to its multiarchitecture index digest. The chart
archive checksum passed, and regenerating its YAML produced identical output.

- The operator installed the controllers and generated GitRepository/root
  Kustomization. Actual Git fetch and subsequent root self-management became
  Ready. Live restrictions, main reference, sparse-checkout paths and required
  cluster-settings substitution matched the configuration. Operator and root
  resource inventories were disjoint, including namespace ownership. The
  validation-only Flux CRD schemas exactly matched the live installed schemas.
- Before database readiness, Kaneo reported DependencyNotReady and had no
  Deployment. After supplying disposable inputs, PostgreSQL 18 and Kaneo became
  healthy, and the FluxInstance, source and all three Kustomizations were Ready.
  The PostgreSQL claim was Bound; Kaneo's loopback health endpoint returned ok.
- A temporary encrypted Secret committed only to the disposable fixture was
  decrypted correctly by Flux using its separate age key. No secret manifests
  or private keys were added to the actual repository.
- API-authorized loopback port-forward served Mission Control. Its resource
  and workload endpoints returned the FluxInstance, PostgreSQL StatefulSet and
  Kaneo Deployment. A dashboard suspend request returned HTTP 405 with actions
  disabled. These are HTTP/API checks, not a full browser usability assessment.
- Probe Pods in both flux-system and an unrelated namespace could reach their
  control HTTP endpoint but could not reach the dashboard Pod on 9080. Live
  generated allow-policy selectors excluded the operator. Port-forward still
  worked. This verifies Pod policy isolation, not provider/host firewall rules.
- Maintenance paused the FluxInstance, root and children, stopped Kaneo and
  captured a native PostgreSQL dump. After resuming reconciliation, Flux restored
  the app to one healthy replica; the database test row remained. Root and
  children returned Ready, and database/app inventories remained disjoint.

Cold image downloads caused temporary readiness timeouts before eventual
success. The runbook explains inspection/retry without reinstalling components
or regenerating credentials. The scratch cluster, Git server, probes,
port-forwards, credentials and kubeconfig were removed after validation.
Existing local development workloads remained running. No VPS, DNS, ingress or
Compose configuration was changed.

## Prior PostgreSQL 18 and Flux rehearsal

A disposable K3s v1.37.2+k3s1 server ran Flux 2.10.0 and fetched a snapshot of
the revised manifests from a local Git HTTP fixture on main. Generated inputs
were disposable. There were no agents, ingress controller or host-exposed
application/database ports. GitHub SSH authentication remains an operator test.

- The source, root, postgres and kaneo reconciliations became Ready. Before
  database credentials were supplied, Kaneo reported DependencyNotReady and
  its Deployment did not exist. After preparation, PostgreSQL became Ready
  before Kaneo was deployed. Their live resource inventories did not overlap.
- Kaneo started without an application PVC under the existing restricted
  runtime settings. SQL verified PostgreSQL 18.6 and data directory
  `/var/lib/postgresql/18/docker`. It ran as `kaneo-postgres-0`, with the
  governing headless Service and exactly one Bound claim, `data-kaneo-postgres-0`.
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
- After maintenance, database, app and root reconciliation resumed and all
  three became Ready again against the Git fixture.

The disposable cluster and its kubeconfig were removed after testing. Existing
local development workloads were left alone.

## Earlier Flux and policy rehearsal

Before the storage revision, a Docker-backed K3s v1.37.2+k3s1 server ran Flux
2.10.0 and the earlier Deployment-based manifests with generated disposable
inputs. It had no agents, ingress controller or host-exposed app/database port.
The detailed policy probes and drift test below were recorded before the
directory/version revision; current Git fetch, ordering and storage behavior
are covered by the separate rehearsal above.

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
