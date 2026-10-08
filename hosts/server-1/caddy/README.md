# caddy

The only HTTPS entry point on the host. Host-networked, bound to its Tailscale
addresses (`CADDY_BIND_ADDRESSES`), with certificates from Let's Encrypt via
Porkbun DNS-01. Caddy owns HTTPS on the host; Tailscale Serve and Funnel must stay
unconfigured (`t3 pair --tailscale` conflicts with this).

## Layout

- `Caddyfile`: global options, the `*.PRIVATE_ZONE` wildcard site, and sites
  that belong to no stack (the host's own identity endpoint, T3 Code on the
  host, Open WebUI redirects).
- `../<stack>/site.caddy`: each stack's own sites. The whole `hosts/<host>`
  directory is mounted read-only at `/etc/caddy/stacks`. `render-sites.sh`
  imports only sites whose referenced routing variables are available.

Host-level settings come from `caddy/.env`; each stack supplies its own
`routing.env`. Caddy loads those files with `required: false`, separately from
application secrets. A missing file/variable skips only its site imports.
The host hostname responds `Caddy is running.` and never proxies to the AI
gateway. Caddy has no startup dependency on the AI gateway.

The custom entrypoint renders the site imports, then runs the explicit Compose
`command`. The image applies `cap_net_bind_service` to a dedicated entrypoint
shell and the rebuilt Caddy binary, keeping that capability across startup
while retaining UID 1000 and `no-new-privileges`. The CI check starts a real
low-port listener with the same user and capability restrictions.

## Wildcard certificate

One certificate for `*.PRIVATE_ZONE` covers every private site. Caddy 2.10+
uses it automatically for any covered site instead of ordering a per-site
certificate, so new service names never appear in Certificate Transparency
logs. Only `OPENWEBUI_LEGACY_DOMAIN`, outside the zone, gets its own
certificate.

## Adding a site

1. Make `<name>.PRIVATE_ZONE` resolve to the host's Tailscale addresses in DNS
   (a wildcard record for the zone covers every name; it is reachable only on
   the tailnet). The zone itself is set in `caddy/.env` (`PRIVATE_ZONE`).
2. Add `<NAME>_DOMAIN` to the owning stack's `routing.env` and
   `routing.env.example`, and add an optional `env_file` entry in `compose.yaml`
   here. Include its routing env file with that stack in the root Compose file.
3. Add the site block to the stack's `site.caddy` with
   `bind {$CADDY_BIND_ADDRESSES}` and `header_up -Tailscale-*`.
4. `bash scripts/validate-stack.sh caddy`, then
   `docker compose up -d caddy` (recreate) or
   `docker compose exec caddy /bin/sh /etc/caddy/render-sites.sh caddy reload --config /etc/caddy/Caddyfile`
   for site-file changes (reload).

## Validate and roll out

```bash
bash scripts/validate-stack.sh caddy-build
bash scripts/validate-stack.sh caddy
cd hosts/server-1 && docker compose up -d --no-deps caddy && docker compose logs --tail=100 caddy
```

The routing check also enforces that reverse proxies listen only on tailnet
addresses, that Loki keeps its authenticated push-only boundary, and that CPA
management paths return 403.

## Data and backup

Certificates and ACME state (`/opt/caddy/data`, `/opt/caddy/config`) are
excluded from backups: Caddy reissues them. Porkbun credentials in
`/opt/caddy/secrets` are backed up.
