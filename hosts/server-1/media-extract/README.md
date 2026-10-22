# Media extraction and library

Nine services integrated into the host's `opt` project, with private HTTPS
routes through Caddy. Images are required private settings: pin the
verified stable digest before deployment, checking required database migrations.
Example tags are validation inputs,
not an upgrade recommendation.

## Paths and least privilege

Runtime configuration and the copied library live under `/opt/media-extract`.
`MEDIA_TORRENT_ROOT` and `MEDIA_RIP_ROOT` may point at retained storage elsewhere;
their real paths belong only in `/opt/media-extract/compose.env`. The migration
does not delete the former stack, payloads or anonymous volumes. Do not start
both projects against the same writable state.

| Service | Writable payload | Read-only payload |
| --- | --- | --- |
| qBittorrent | `/data/torrent` | none; shares Gluetun network namespace |
| Sonarr | `/data/media/tv` | `/data/torrent`, both `/data/import/*` sources |
| Radarr | `/data/media/movies` | same three import sources |
| Jellyfin | cache only | `/data/media` |
| MakeMKV | rip `/storage`, `/output` | device-adapter script |
| HandBrake | `/output`, `/watch`, `/trash` | MakeMKV output at `/storage` |

Each service also writes its own config. Bind sources must exist before startup
(`create_host_path: false`); restore them with numeric owners, ACLs and xattrs.
FlareSolverr config and HandBrake watch/trash use explicit auxiliary binds rather
than new anonymous volumes. Never grant source write access to hide import errors.
Provision HandBrake's copied watch/trash directory roots for the configured app
UID/GID; inherited root-owned volume roots can be readable but not writable.
Validate with the real app user instead of recursively changing payload owners.

qBittorrent uses `/data/torrent/complete` and `/data/torrent/incomplete`.
Sonarr/Radarr use the same paths; no Remote Path Mappings. Their download client
is `gluetun:8080`, not the host. Admin ports 8080, 8989, 7878, 9696, 5800 and
5801 remain on loopback, as do Gluetun's control API on 18000 and FlareSolverr
on 18191. Only Jellyfin HTTP 8096 is also published to the LAN.
VPN failure must leave qBittorrent without a
non-VPN route. Do not attach it to another network.

## Private HTTPS

Set all nine `MEDIA_*_DOMAIN` values in `/opt/media-extract/routing.env`, using
`routing.env.example` as the template. `env-files.tsv` maps the ignored checkout
symlink to that file; the root Compose include and Caddy load it using the same
convention as other stacks. Create DNS records resolving each name to this
host's Tailscale addresses. The existing wildcard certificate covers names
inside `PRIVATE_ZONE`; no deployment domain is stored in Git.

| Routing variable | Backend |
| --- | --- |
| `MEDIA_JELLYFIN_DOMAIN` | Jellyfin, loopback 8096 (LAN HTTP also retained) |
| `MEDIA_QBITTORRENT_DOMAIN` | qBittorrent WebUI, loopback 8080 |
| `MEDIA_GLUETUN_DOMAIN` | Gluetun control API, loopback 18000 |
| `MEDIA_PROWLARR_DOMAIN` | Prowlarr, loopback 9696 |
| `MEDIA_SONARR_DOMAIN` | Sonarr, loopback 8989 |
| `MEDIA_RADARR_DOMAIN` | Radarr, loopback 7878 |
| `MEDIA_FLARESOLVERR_DOMAIN` | FlareSolverr API/status, loopback 18191 |
| `MEDIA_MAKEMKV_DOMAIN` | MakeMKV GUI, loopback 5800 |
| `MEDIA_HANDBRAKE_DOMAIN` | HandBrake GUI, loopback 5801 |

Caddy binds these routes only to the host's Tailscale addresses, strips
client-supplied `Tailscale-*` headers, and forwards WebSockets for the desktop
GUIs and Jellyfin. Arr login redirects are upgraded to HTTPS because those
backends can generate HTTP URLs from their local proxy connection.
Existing application logins still apply. Gluetun has no web
dashboard: its root redirects to `/v1/vpn/status`. Configure native control API
authentication through `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` in the private
`vpn.env`; never disable authentication to make the route work. FlareSolverr
also provides an API rather than a dashboard. The optical services must already
be running under the `rip` profile for their routes to respond.

After changing routing values, recreate only Caddy to load its environment.
Site-only edits can use the normal renderer/reload command in the runbook.
If any media routing value is missing, the renderer skips this stack's site
file while other stacks remain available. The routing file is backed up with
the media configuration.

## Importing and playback

Raw MakeMKV output is staging, not library media. HandBrake encoding remains
manual. Sonarr/Radarr copy finished media to their own library directory before
Jellyfin scans it. For Sonarr manual imports, explicitly choose Interactive
Import → **Copy/Hardlink**, not Move. The browser's remembered import selection
is not a server-wide ban on Move; the read-only source enforces the boundary.

Retain Sonarr's `copyUsingHardlinks=false`, `setPermissionsLinux=false`, enabled
completed-download handling, category `tv-sonarr` and completed-download removal
disabled. This avoids automatic move/cleanup of retained torrent sources.
Separate Radarr follow-up: its existing hardlink and completed-removal settings
were not changed by migration; review before importing the next movie.
Hardlinks require a separate storage/permissions design and are not enabled as
a workaround here. Independent copies intentionally cost extra space.

## Operation

From `hosts/server-1`, use explicit service names, never whole-project down/up:

```bash
docker compose up -d --no-deps --pull never gluetun
# Wait for Gluetun healthy before starting qBittorrent.
docker compose up -d --no-deps --pull never qbittorrent
docker compose up -d --no-deps --pull never prowlarr flaresolverr sonarr radarr jellyfin
docker compose --profile rip up -d --no-deps --pull never makemkv handbrake
```

Do not operate this stack through Komodo until its execution environment can
resolve **all** private bind sources (including retained staging storage).
Do not broaden management mounts or run a host-wide Deploy as a shortcut.
Preserve application settings, UIDs and ingress during migrations. Image updates
are a separate explicit operation: retain the previous image and stopped config
copy, review release requirements, and validate application state after startup.

## Optical reader

The `rip` profile is opt-in. Set both required device paths to stable host
aliases, not boot-dependent kernel names. The block alias stages at
`/dev/media-extract-sr`, the generic alias at `/dev/media-extract-sg`.
The adapter resolves current kernel names through sysfs, passes actual device
GIDs to `SUP_GROUP_IDS`, then execs `/init`. Keep long-form device mappings.
Check read/write DAC access as `app`, never only root; do not read a disc as a
permission test.

`systemd/99-media-extract-optical.rules` creates the serial-derived generic
alias. The early `59-*.rules.example` is an opt-in reader workaround: put the
actual serial only in the private installed rule and its recovery copy under
`/opt/media-extract/hardware`. It must precede `60-persistent-storage.rules`,
setting `UDEV_DISABLE_PERSISTENT_STORAGE_BLKID_FLAG=1` for the affected reader.
It disables filesystem probing, not identity/by-id rules or direct ripping.
Never run `blkid`, bare `udevadm test`, repeated physical retries or reload
rules blindly on a mounted/in-use disc. Leave an empty tray open for a cutover
so the automatic ripper cannot restart a job. A wedged drive needs diagnosis,
not repeated probes.

## Recovery and validation

`backup.yaml` captures private env files, application config, auxiliary state
and private hardware recovery copies. SQLite is exported online by the host
runner. Library, torrents, raw rips, transcodes and cache are intentionally
excluded. Add any retained nonstandard storage roots to the private host
declaration; a successful coverage check does not prove an offsite restore.

Restore config and consistent SQLite exports before startup, retain ownership
and modes, provision all mount roots, then restore hardware rules and env links.
Do not restore an example over existing credentials. The original preserved
tree is a point-in-time rollback source, not a synchronized replica. Stop the
new writers before rollback and decide how to retain any newer changes.

Run `./scripts/ci-validate.sh` and the full root suite; validation selects a
cached CI Compose without changing the host plugin. `scripts/check-media.py`
checks VPN, listeners, mount access, optical
handoff and fail-closed binds; mutation tests guard against regressions. Live
validation must also check app identities/counts, actual read/write denial,
VPN egress, and representative Jellyfin file access. No disc rip is required.
