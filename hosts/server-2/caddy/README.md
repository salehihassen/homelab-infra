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

## Kaneo ticket templates

`kaneo-new/` is a static form served at `/new/` on the Kaneo hostname. Open
`/new/` to pick a template, or link straight to one, such as `/new/triage`,
`/new/bug` or `/new/task?area=homelab`. `title` and `group` query parameters
prefill those fields too. The page calls Kaneo's API with the viewer's own
session cookie, so it holds no credential, and it creates a ticket only when
the form is submitted. Templates, areas and description skeletons live in
`kaneo-new/templates.json`; every label it names must already exist as a
workspace label in Kaneo. File changes are live on the next page load, but a
new mount or Caddyfile change needs the Caddy container recreated.
