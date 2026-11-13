# Migration roadmap and preserved decisions

Recorded 2026-11-09 from the supplied plans:

- `homelab_k3s_gitops_handoff_2026-11-06.md`: public/private isolation,
  GitOps, placement, backups and incremental migration.
- `k3s-public-cluster-fault-tolerance-handoff.md`: future public application
  availability during loss of the sole server VPS.

These remain the target architecture. The current public-only MR is the first
step, not a replacement for either plan. The user's later scope decision defers
the private pilot to a separate MR. The later fault-tolerance plan refines the
public availability goal and rules out Tailscale for public inter-node transport.
Original private host names and deployment details are deliberately omitted.

## Decisions to preserve throughout migration

- Build two independent clusters: public-edge on offsite public-workload hosts,
  and a later private-home cluster on trusted home hardware. Never join the
  private server to the public cluster, or the public server to the private
  cluster. No cross-cluster administrative trust or shared node tokens/kubeconfigs.
  Keep public nodes offsite initially. Any future home public worker requires
  a deliberately isolated host/VM with no private mounts or broad LAN access;
  a VLAN alone does not isolate a compromised host from its hypervisor.
- Use the same protected Git repository with separate Flux reconciliations,
  read-only deploy identities, age keys and backup credentials. Shared Git is
  still a shared supply-chain dependency; protect its permissions and checks.
  Keep real identifiers and plaintext secrets out of public Git.
- Start with one K3s server per cluster, also running workloads. The public
  server uses SQLite. Add agents for demonstrated capacity/availability needs;
  do not introduce extra control-plane servers, etcd quorum, an external HA
  datastore or a service mesh for this initial availability goal.
- Keep existing Compose services, public routes and Restic/B2 recovery working
  while introducing one new service or migration at a time. Nextcloud AIO and
  other Docker-managing applications remain in Compose until a supported
  alternative or migration is selected.
- Keep public inter-node transport independent of Tailscale. Select and test
  a documented K3s-compatible encrypted multicloud path before enrollment;
  wireguard-native is a candidate from the second plan, not a configured fleet
  assumption. Existing administrator VPN access and explicitly approved future
  application-level cross-boundary calls are separate decisions from CNI.
- Allow public-to-private access only for individually approved authenticated
  application endpoints. Deny broad LAN/private-network access and Kubernetes
  administration. Verify host/provider firewalls, router rules, VPN grants and
  Pod policies independently. No cross-cluster routing is installed now.
  Restricted Tailscale access for those application calls remains an option
  from the first plan; it must not become public-cluster inter-node transport.
- Respect hardware and data ownership. Future NAS, USB, optical and GPU apps
  use required node placement and suitable local storage/device access. An
  intermittently offline NAS may take its apps offline; no silent relocation.
  Host boot, mounts, disk unlock, networking and K3s remain host prerequisites.
- Preserve restricted workloads, default-deny policies, explicit exposure,
  versioned images and resource/health settings. Add observability to the
  existing stack and submit dependency updates through reviewed PRs. Verify
  policy enforcement and restore behavior rather than relying on manifests.
- Back up native database exports, matching application files and stable keys,
  plus cluster datastore/config, server token and recovery credentials. Maintain
  separate recoverable key copies and test restores. Git and retained local
  volumes alone are not a recovery strategy.
  Public backup credentials must not grant deletion or decryption of private
  backups.

## Step 1: current MR, new public Kaneo only

One public server hosts a fresh Kaneo Deployment with ephemeral runtime storage
and a shared PostgreSQL 18 instance run by the CloudNativePG operator, with a
retained local volume;
Kaneo gets its own role and database there. The database has its own
directory/Flux reconciliation, which Kaneo depends on.
No existing private Kaneo data/credentials are imported.
Flux Operator manages the two Flux controllers and generated Git/root sync;
its read-only Mission Control UI is reached by port-forward or the tailnet.
No extra GitOps engine or public dashboard route is introduced. See
[GitOps ownership](gitops.md).
Prepare external inputs before starting active Flux reconciliation. Claim the
initial administrator through local access and add only its new Caddy site
after application, policy and recovery acceptance. All existing services stay
in Compose. No private-cluster provisioning or billable capacity is added.

Exit: public Flux fetch/reconciliation and drift repair, app authentication and
CRUD, local restart persistence, enforced policy boundaries, a complete isolated
restore drill, and optional public HTTPS all pass; existing Compose services
still work. Local rehearsal results and remaining fleet checks are recorded in
[validation](validation.md) and [testing](testing.md).

## Step 2: private-cluster pilot in a separate MR

Create the independent private root and credentials on the selected always-on
home server. Start with one simple, non-sensitive service; choose the workload
in that MR. Keep personal data in existing deployments until the private pilot
proves GitOps, intended private access, denied public access and recovery.

Exit: two distinct trust domains and Flux roots with no shared administrative
credentials. Cross-boundary API integrations remain separate explicit work,
using disposable endpoints to prove allowed and denied reachability first.

## Step 3: public agents and encrypted networking

Add offsite agents only when useful, including a future ARM64 VPS if available.
Do not assume provider capacity, free-tier eligibility or particular addresses.
Validate the chosen CNI, encryption, firewall ports, pod/service routes, MTU,
DNS, direct connectivity, network partition recovery and policy enforcement
across the actual providers. Test images on ARM rather than relying only on
their published image indexes. Label failure domains for later placement.

Exit: healthy cross-node workloads and explicitly tested network boundaries.
Adding workers leaves Kaneo's local data on its current owner. It does not by
itself meet the next phase's availability goal.

## Phase 2: public serving continuity with one control-plane server

The target is deliberately limited: when the entire server VPS disappears,
selected apps already running on healthy agents keep serving through their
normal public hostname while the operator repairs the server. Do not promise
new deployments, scaling, rescheduling, controller-driven endpoint updates or
recovery from subsequent workload/ingress failures during the outage.

Implement this in later reviewed changes:

1. Run ingress and selected application replicas on surviving provider nodes
   before the failure. Introduce redundant Traefik with verified placement;
   customize packaged Traefik through HelmChartConfig. ServiceLB placement
   alone does not establish that an ingress replica exists on a node.
2. Put Cloudflare Load Balancing outside Kubernetes, using HTTPS origins,
   validated certificates and application-specific end-to-end health checks.
   Verify SNI/hostname handling, origin access controls and measured failover
   delay. Recheck pricing and plan capabilities before provisioning. A managed
   edge outage remains a dependency of the normal hostname.
3. Remove the failed VPS from each selected app's critical runtime dependency
   chain: databases, volumes, DNS, sessions, authentication/token dependencies
   and certificates as applicable. Choose an explicit data-availability design
   per app. Kaneo's app replicas can use shared PostgreSQL, but its single
   PostgreSQL writer still has node-bound data. Add shared Redis for realtime
   broadcasts when scaling Kaneo, and shared S3-compatible storage if uploads
   are enabled. Some apps may intentionally remain single-node.
4. Test stale Service endpoints and ingress behavior with the API unavailable.
   Keep ordinary ClusterIP Services by default. Use internalTrafficPolicy:
   Local only for selected apps with ready local replicas and a verified
   ingress-to-Service path. externalTrafficPolicy: Local concerns ingress
   arrival and does not establish local application backend selection.
5. Power off the entire server VPS and probe the normal public hostname for
   at least an hour. Record success rates, latency and failover/recovery times;
   exercise authenticated requests, database access and streaming/websockets
   where used. Test app-container and Traefik restarts while the API is absent
   and document the actual limitations. Restore the server, verify clean
   reconciliation, and repeat after relevant K3s/ingress/CNI upgrades.

Exit: measured serving continuity for named apps under the tested failure,
with documented limits. Revisit control-plane HA only if tests or new goals
require management, rescheduling or broader failure recovery during outages.

## Deliberate staging choices, not final topology

- **Ingress:** the second plan diagrams Traefik/ServiceLB immediately. This MR
  retains Compose Caddy and disables them to preserve existing port ownership,
  as the first plan requires during transition. Before phase-2 acceptance,
  selected apps must have redundant ingress on surviving origins without the
  original Caddy VPS in their required path. Plan the 80/443 handoff explicitly.
- **Networking:** initial VXLAN is for the one-node pilot. Review the CNI change
  before agent enrollment; do not expose VXLAN publicly or join nodes under
  the assumption that VPN host connectivity provides Pod networking.
- **Data:** the local retained PostgreSQL claim is an intentionally temporary
  availability limit. Capacity growth does not resolve it. Storage/DB
  availability must be addressed before claiming continuity for Kaneo.
- **Secrets:** operator-managed inputs bootstrap the pilot; SOPS/age support
  is prepared. Add encrypted resources deliberately once independent keys and
  recovery are proven, without sharing the future private cluster's key.

## Continue migrating gradually

After pilots, choose each next service by exposure, data sensitivity, hardware
ownership and recovery complexity. Each migration gets an application-consistent
backup, isolated restore, authentication/policy checks, observability and an
explicit rollback decision for any writes after cutover. Hardware-bound apps
can move with node pinning; Docker orchestration requirements remain a valid
reason to retain Compose. Neither this roadmap nor a successful local render
authorizes live firewall/DNS changes, data migration or provider purchases.
