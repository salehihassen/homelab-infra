# ai-gateway

CLIProxyAPI (`CPA_DOMAIN`), CPA Manager Plus (`CPAMP_DOMAIN`), and the AI policy
proxy (`POLICY_PROXY_DOMAIN`). The host's own hostname belongs to Caddy and
does not serve the policy proxy.

- **Provides:** internal network `cpa_policy_model` (`opt_cpa_policy_model`,
  no internet egress) and service `ai-policy-proxy`. Apps join the network and
  call `http://ai-policy-proxy:18443/v1` with their own client key.
- **Consumers:** jobsmith, condenseit, speakable, email-assist. Caddy routes to
  the gateway but has no startup dependency on it. Jobsmith demos also join
  `opt_cpa_policy_model` by name, so keep the name stable.
- **Networks:** fixed subnets and addresses come from `CPA_*` and
  `AI_POLICY_PROXY_IP` in this stack's `.env` and `routing.env`; Caddy reaches
  CPAMP and the policy proxy at those addresses. The routing upstream values
  derive from their corresponding IP variables in the same env file.
  `CPA_POLICY_DYNAMIC_RANGE` is a subset of `CPA_POLICY_SUBNET` that excludes
  every fixed gateway address. Client containers allocate only from this pool,
  so they cannot take a gateway address when Docker starts them first.
- **Readiness:** The policy proxy checks both its local catalog and CPA's HTTP
  endpoint; an unavailable CPA
  marks the proxy unhealthy instead of allowing dependent apps to start.
- **Data:** `/opt/cpa` (config, auth, usage databases, secrets, runbook in
  `/opt/cpa/docs/README.md`).
- **Backup:** `/opt/cpa`, with SQLite online copies of the CPAMP usage and
  policy affinity databases.

Provider OAuth callbacks still use SSH local forwards to the loopback ports
1455, 54545 and 51121.

## Shared model modalities

Keep CPA on its stock image. Policy Proxy 0.5.0 (or a reviewed build containing
`/v1/upstream-models`) reads `/config/modalities.json` through its existing config
mount. Caddy sends only GET/HEAD requests for the public CPA `/v1/models` to that
endpoint, preserving credentials and query strings. The Policy Proxy URL still
serves its policy catalog at `/v1/models`. CPA's direct/internal listener keeps
its stock schema; inference and callbacks continue to reach CPA directly.

The generated file holds capabilities, not model availability or credentials.
Use the refresher and deployment templates from the same Policy Proxy source
revision (`deploy/README.md`); do not maintain a second copy here. Its inputs are
the CPA config and policy catalog, and its output is the separate
`/opt/cpa/config/ai-policy-proxy/modalities.json`. Refreshes must not rewrite
either input. Unknown capabilities are exposed as `null`; source outages retain
previous metadata. Runtime requests do not contact metadata sources.

### Test before switching traffic

Use a separate infra worktree: the running Caddy mounts this repository's site
files, so editing its active checkout can affect the next reload. Validate the
candidate with `bash scripts/validate-stack.sh compose`, then `caddy-build` and
`caddy`. These use example environments and temporary containers.

For a live smoke test, build the candidate Policy Proxy image and generate
metadata into a temporary directory. Run a second Policy Proxy on an unused
loopback port with copies of the catalog and metadata, a temporary HMAC secret
and affinity database, and CPA's direct loopback upstream. Run a temporary Caddy
using the candidate site's routes on unused loopback HTTP ports, with automatic
HTTPS and its admin API disabled. Do not use production ports, databases or
Compose project names. Send only catalog/health/denial requests; no inference is
needed. Remove both temporary containers afterward.

Check both model-list URLs, invalid-key rejection, HEAD, unchanged Codex
`?client_version=` catalogs, management denials, and direct CPA routing for
non-model paths and POST `/v1/models`. CPA inventory, order and existing fields
must match the requesting credential's stock response. Record production
container IDs/start times and config checksums before and after to confirm no
production restart or config change occurred.

### Activate after service approval

1. Publish the reviewed Policy Proxy image and select its immutable digest in
   the private `AI_POLICY_PROXY_IMAGE` setting. CPA's image stays unchanged.
2. Install the refresher, source rules and dependencies from the reviewed service
   revision. Generate the metadata file once and verify it is readable by the
   Policy Proxy container user. Replace any older refresh unit with the service
   repository's current template; leave the timer disabled until the first run
   succeeds. The new refresher does not accept the old `--apply-policy` option.
3. Recreate only Policy Proxy using the existing stack environment and Compose
   project (`up -d --no-deps ai-policy-proxy`). Preserve its affinity volume and
   secrets. Verify both `/v1/models` and `/v1/upstream-models` directly before
   activating the Caddy change. This step briefly interrupts Policy Proxy clients.
4. Apply the reviewed infra revision to the active checkout, validate Caddy with
   the real environment, and gracefully reload it using the host runbook. Do not
   recreate CPA or the whole stack. Verify both public model-list URLs, then
   enable the service-owned refresh timer (every six hours).

Policy Proxy's CPA upstream must stay the direct internal CPA address to avoid
a routing loop. Public CPA model-list availability now also depends on Policy
Proxy; ordinary CPA inference retains its existing route. Roll back the Caddy
model-list route first, then restore the old Policy Proxy image and disable the
refresh timer. Keep the existing affinity database and credentials.
