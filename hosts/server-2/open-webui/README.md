# open-webui

Managed by the host project. Runtime settings are in `/opt/open-webui`; `.env`
and `routing.env` are private symlinks. Existing data paths are selected through
interpolation settings and must exist before startup. The example backend binds
to loopback; live listener settings are private deployment choices.

Images are pinned to the deployed digest (or a retained local image ID) in the
private env file. Adoption preserves the application version and stored keys.

Run `docker compose up -d --no-build --pull never open-webui` from the host
directory and inspect its health. See [the host playbook](../PLAYBOOK.md) for
backup, restore, verification, updates and rollback.
