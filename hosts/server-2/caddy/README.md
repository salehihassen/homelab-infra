# Public ingress

Caddy owns host HTTPS and uses public ACME with its existing external data
volume. Every deployment hostname and redirect target is supplied through
private routing settings. The tracked file contains only variable names.
Only routes for active applications and existing static redirects are retained.

Backends use loopback ports or configured private upstreams. The AIO controller
uses a self-signed HTTPS certificate; verification is disabled only for that upstream.
Incoming Tailscale identity headers are stripped from every application proxy.
Validate with `caddy validate` before reloading. Do not copy private certificate
keys into Git. See [the playbook](../PLAYBOOK.md).
