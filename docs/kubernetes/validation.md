# Initial validation record

Prepared 2026-11-08. These are local rehearsal results, not deployment evidence
for the real fleet. No live credentials, host config, DNS or ingress changed.

- Both cluster roots, both app paths, and the manual data helper render with
  Kustomize 5.8.1. Kubeconform 0.8.0 checks Kubernetes 1.37 schemas and the exact
  vendored Flux custom-resource schemas. Generated upstream CRD definitions
  are excluded from Kubeconform and were accepted by the actual K3s API.
- Cluster/source path isolation, public Kaneo suspension, protected persistence,
  restricted runtime, health/resource settings and absence of public workload
  listeners pass the repository's migration contract checks.
- A disposable Docker-backed K3s v1.37.2+k3s1 server ran the shared Flux 2.10.0
  controllers, private Podinfo, and public Kaneo/PostgreSQL manifests. Both
  production workload definitions were exercised in that one **test** cluster;
  this does not collapse the two distinct production reconciliation roots.
- Podinfo and Kaneo health endpoints respond successfully. Kaneo and PostgreSQL
  run as non-root with read-only root filesystems, dropped capabilities,
  seccomp, default-deny policies and bound retained local PVCs. Kaneo survives
  an application restart with the same data volumes.
- A node-origin HTTP request reaches Kaneo's ClusterIP, exercising the proposed
  same-host Caddy upstream. The opt-in Caddy template also validates with inert
  example settings. An unrelated pod is denied, allowed by a temporary
  test-only ingress exception, then denied again after removing it. A separate
  target with allowed ingress remains unreachable from Kaneo because of its
  egress policy. Test allowances are removed after checks.
- A native PostgreSQL custom-format dump imports successfully into a fresh
  scratch database (45 public tables). The manual app-data helper mounts the
  same PVC under its restricted UID and can write/read/archive it while the
  application is stopped. Its archive extraction flags pass a scratch tar
  round-trip, and the app reads the PVC marker after restart. This is a smoke
  test, not a restore of real data.
- Both complete Flux roots build using strict substitution from an actual
  test-cluster `cluster-settings` Secret and pass server-side API dry-run,
  including all vendored controller/CRD resources. No external Git source is
  cloned and no real cluster is bootstrapped in this test.
- Existing Compose/dependency/network checks and 83 Python tests pass (one
  existing skip). Caddy routing/security, Alloy formatting/configuration and
  its 76-span normalization harness, Prometheus config/rules/tests, Loki config,
  and the separate public-host suite pass in an Ubuntu validation environment.
  The local workstation uses NixOS; the existing tests assume Debian shell and
  filesystem conventions, so they were run as a regular user in Ubuntu with
  host Docker networking and readable public configuration fixtures.

ARM64 support was checked in image index manifests for the workload images;
no ARM runtime was available. Real SSH deploy-key authentication, independent
production credentials, cross-node/provider CNI, real storage sizing,
Tailscale/firewall denial, public DNS/TLS/authentication, scheduled backup
capture, and a real application restore remain operator acceptance steps in
the bootstrap and Kaneo runbooks. The disposable rehearsal is removed after
validation; the repository never provisions a paid machine.
