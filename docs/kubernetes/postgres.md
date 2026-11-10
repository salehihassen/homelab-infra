# Public PostgreSQL ownership and upgrades

`kubernetes/databases/postgres` owns the PostgreSQL 18 StatefulSet, database
Services, retained storage class, namespace and baseline/database policies.
`kubernetes/apps/kaneo` owns only Kaneo's Deployment, Service and egress policy.
Flux reconciles the database through `postgres`; `kaneo` uses `dependsOn` to
wait for that reconciliation to be Ready. Both paths are
included in the public Git source; neither owns resources from the other path.

The initial database still serves only Kaneo. Its existing namespace `kaneo`,
StatefulSet `kaneo-postgres`, Services, labels, credential reference and claim
`data-kaneo-postgres-0` stay the same. Keeping these identities separates Git
ownership without introducing a namespace or data relocation. The database
path owns the existing namespace and its default-deny policy so it can start
before the application. External inputs are prepared using [kaneo.md](kaneo.md).

The pinned image is `postgres:18.6-alpine` with a multiarchitecture index digest.
For PostgreSQL 18 the data volume mounts at `/var/lib/postgresql`, and PGDATA
is `/var/lib/postgresql/18/docker`, matching the official image layout. It runs
as the image's postgres UID/GID 70 with the existing restricted settings.
The 10 GiB claim and local storage retain data across Pod/controller replacement,
but remain tied to the selected storage node. One replica is intentional;
increasing the StatefulSet replica count does not configure database replication.

## Earlier installations and major versions

This branch prepares a fresh public instance and has not changed a real VPS.
An empty claim can initialize on PostgreSQL 18 directly. Existing PostgreSQL 16
data cannot be upgraded by changing the image tag or moving YAML files.
Use a tested native export/restore into a fresh PostgreSQL 18 candidate, or a
separately designed pg_upgrade procedure. Keep the original recovery set and
disk until the new instance passes acceptance. Never start 18 on a 16 data
directory or delete claims to force initialization.

For an earlier Flux installation, plan ownership transfer and major upgrade as
separate changes. Keep the installed image and storage layout unchanged during
the ownership move. Verify that the source Kustomization's `prune: false` is
acknowledged in-cluster before moving files. Preserve resource names and
namespace, reconcile the destination, then reconcile the source and inspect
both inventories. Prove there is one database controller and one owner for
every resource. An old Deployment must be deliberately retired; adding a
StatefulSet does not remove it. Use the official Flux move procedure linked
below and a separate tested data migration plan before adopting version 18.
No automatic live upgrade or data migration is installed here.

## Adding database clients later

Use this directory as the database management boundary when other public apps
need PostgreSQL. Provision separate databases and restricted roles per app,
separate credentials and explicit NetworkPolicy allowances. The current
image-initialized `kaneo` role is the dedicated instance's bootstrap/admin
role; do not reuse it for additional clients. Provision application roles
deliberately before turning this instance into shared infrastructure.
Independent per-app instances remain an option if isolation is more useful
than sharing. Public and private clusters must keep separate database trust
and recovery credentials.

Database backups and restore drills use [Kaneo recovery](kaneo.md) today. Revise
them for every additional database, role and client before accepting its data.
Database replication, surviving storage and server-outage continuity remain
later work in the [roadmap](roadmap.md).

References: [official PostgreSQL image](https://hub.docker.com/_/postgres),
[PostgreSQL major upgrades](https://www.postgresql.org/docs/18/upgrading.html),
and [Flux resource moves](https://fluxcd.io/flux/faq/).
