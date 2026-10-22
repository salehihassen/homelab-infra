# Media safety boundaries

- Never interrupt a rip, encode, import, download or playback session. Inspect
  runtime and diffs first; recreate only the named affected services.
- Run `./scripts/ci-validate.sh` here and the full infra suite before handoff.
- Preserve VPN namespace routing, loopback admin ports, read-only sources and
  the shared `/data/torrent` path. Do not add Remote Path Mappings.
- Import staging media through Sonarr/Radarr; keep HandBrake manual.
- Preserve long-form optical devices, stable aliases, adapter `/init` handoff
  and actual supplementary device groups. Verify as the app user, not only root.
- Never probe optical media with `blkid` or bare `udevadm test`. Verify the early
  probe-disable rule before physical retries; stop if a reader wedges.
- All deployment identifiers and state belong in private runtime settings, not Git.
