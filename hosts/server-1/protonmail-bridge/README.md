# protonmail-bridge

Proton Mail Bridge with its noVNC GUI (`MBRIDGE_DOMAIN`) and an HAProxy IMAP
gateway for allowlisted tailnet clients. Both use host networking.

- **Image:** `PROTONMAIL_BRIDGE_IMAGE`, published to GHCR by the
  protonmail-bridge-docker repository (helper scripts, login runbook). That repo's `compose.yaml` is superseded by this one.
- **Consumers:** email-assist reads IMAP on host loopback `1143` through its
  socket relay; the Home Assistant mail-count exporter reads it directly.
- **Data:** `/opt/protonmail-bridge` (Bridge state, keyring and VNC secrets,
  rendered `mail-gateway/haproxy.cfg`).
- **Backup:** the whole directory, with SQLite online copies of the Gluon
  databases. The nightly job also saves the built image so a restore does not
  depend on future apt indexes.
