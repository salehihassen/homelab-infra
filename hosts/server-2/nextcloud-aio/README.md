# Nextcloud All-in-One

Only `nextcloud-aio-mastercontainer` is Compose-managed here. AIO retains
exclusive management of its children, their images, networks and resource
settings. Preserve the mandatory controller container name and external
`nextcloud_aio_mastercontainer` volume name. Do not import the child containers
as Compose services.

The existing user-data bind path is selected by `NEXTCLOUD_DATADIR` in the
private `/opt/nextcloud-aio/app.env`. Do not change it as part of adoption.
The controller needs its Docker socket and writable root filesystem. A read-only
socket bind still permits Docker API writes; this is an administrative trust
boundary required by AIO.

The backup runner coordinates maintenance, native PostgreSQL globals/database
exports, ownership/grants, Redis SAVE and matching files. Live PG files are
never ordinary backup inputs. Additional active AIO volumes fail coverage checks
until explicitly declared. See [PLAYBOOK.md](../PLAYBOOK.md).
