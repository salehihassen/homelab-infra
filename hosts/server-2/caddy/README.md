# Public ingress

Caddy owns host HTTPS and uses public ACME with its existing external data
volume. Every deployment hostname and redirect target is supplied through
private routing settings. The tracked file contains only variable names.
Only routes for active applications and existing static redirects are retained.

Backends are host loopback ports. The AIO controller uses HTTPS with its local
self-signed certificate; the exception is limited to that loopback upstream.
Incoming Tailscale identity headers are stripped from every application proxy.
Validate with `caddy validate` before reloading. Do not copy private certificate
keys into Git. See [the playbook](../PLAYBOOK.md).
