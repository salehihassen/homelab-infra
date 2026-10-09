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
