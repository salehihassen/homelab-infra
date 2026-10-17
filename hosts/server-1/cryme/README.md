# Cryme (experimental)

Original multiplayer Arlington sandbox. This directory owns the private HTTPS
route only. The game runs as an independent Compose project on loopback 8910;
Caddy proxies WebSocket connections on the same origin. All tailnet users who can
reach the host may join; there is no separate application login. Player progress
uses random browser profile tokens.

Runtime configuration and JSON progress live outside this checkout in the
service's runtime directory. No database or Docker volume is created, and no
runtime backup is enabled. An inactive backup manifest lives beside the host's
other experimental manifests. Source and this route remain covered by the
existing repository backup policy. The game repository documents operations.
