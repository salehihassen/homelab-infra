# Forgejo migration handoff

Forgejo, its runner and its dedicated Docker-in-Docker daemon are retired from
server-1. Their Compose include, networks/secrets, Caddy route and routing inputs,
env-file mappings, inventory entry and stack backup declaration are removed.
The future NAS host is not adopted into this repository yet. This change does
not deploy or start Forgejo there.

The migration backup is captured with all three services stopped. It contains:

- The native PostgreSQL custom-format `forgejo` dump and cluster globals,
  including role credentials. The globals contain other cluster roles too;
  treat the entire backup as secret.
- Forgejo repositories, application config, environment files and secrets;
  runner registration, config, certificates and token.
- The original deployment definitions, container/image inventory and exact
  Forgejo, runner, Docker-in-Docker and PostgreSQL images.
- Capture metadata, file checksums and restore instructions.

CI daemon image/build caches and filesystem `lost+found` are excluded. The
archive is encrypted, copied to the NAS backup vault and verified there. Exact
paths, decryption-key locations and verification evidence are recorded privately
in `local/site.md` and the vault handoff; never commit those details or keys.
The archive and recovery key are separate files. Escrow the key independently
before relying on the NAS as the only recovery host.

Original runtime files and the stopped database remain on server-1 for rollback;
no volumes, repository data, database or historical Restic snapshots are deleted.
The retained database has temporary coverage in the host's private local backup
declaration. Remove that entry when the database is intentionally deleted after
migration. Active nightly backups no longer pause Forgejo or stage its files.
The one-time NAS archive does not establish recurring NAS backup coverage.

For migration, decrypt and verify into a new private directory, load the saved
images and extract runtime files with numeric owners, ACLs and xattrs preserved.
Restore globals into an empty matching-major PostgreSQL cluster with a unique
temporary bootstrap administrator, then restore `forgejo.dump` with
`pg_restore --exit-on-error --create`. Review globals before importing them into
an existing cluster. Update database addressing, routing, SSH and runner settings
for the new host; do not run the old Compose file against production paths.
Keep runners offline until Forgejo's database/files, credentials, repository
clone/push and package payloads have been verified and the replacement is
intentionally promoted. Keep the old host offline throughout cutover.

For rollback, recover the old stack definitions from Git or the archive, restore
its ignored env symlinks using the saved mapping/settings, validate Compose and
backup coverage, then start only these three services and restore Caddy's route.
Do not run both copies against the same database or enable both runners.

The migration capture passed an isolated restore on the NAS: native PostgreSQL
import, restored-password authentication, matching row counts, Forgejo health
and API access, recovered-repository clone/`git fsck`, and a disposable repository
create/commit/push/clone/delete cycle. No packages existed to test payload
recovery. The runner and CI daemon were not started during the drill.
