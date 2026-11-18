# Twenty CRM on the public cluster

Twenty v2.59.0 is staged for `public-edge`, in namespace `twenty`. **It is off:**
`twenty-server`, `twenty-worker` and `twenty-redis` each declare `replicas: 0`.
Flux can apply those controllers without the private inputs. Redis creates no
PVC until first started. There is no public route, DNS change or migration job.
The separate `twenty-database` Flux Kustomization is suspended, so its missing
password cannot hold up the shared PostgreSQL reconciliation or Kaneo.

## One instance, separate businesses

`IS_MULTIWORKSPACE_ENABLED=true` enables Twenty's built-in workspace model.
Create **one workspace per business**, with separate membership, records,
attachments, API keys and integration credentials. Twenty stores workspace
records in separate PostgreSQL schemas, while identity and instance metadata
share its `core` schema. It uses one application database/role (`twenty`), one
Garage bucket (`twenty-uploads`, with workspace paths), and one Redis queue.
This is application-level separation within a shared service: the instance
operator and application process remain trusted across all workspaces.

Members of business A must never be invited to business B unless that access is
intentional. Keep automatic access-domain enrollment off and issue API keys
inside the appropriate workspace. Business workspace admins should not receive
instance-level `canAccessFullAdminPanel` or `canImpersonate` privileges.

Both processes share `twenty-config`, private `twenty-settings` and
`twenty-credentials`. Configuration is environment-controlled, preventing
database settings from silently overriding the workspace mode. Workspace
creation is limited to server admins; the empty-instance bootstrap is an
exception. Billing is disabled; no enterprise subscription is configured.

## Addressing and public ingress

For example, set **`SERVER_URL=https://crm.private.example`** in the private
settings file: this is the base domain, without the `app.` prefix. With
`DEFAULT_SUBDOMAIN=app`, login is at `app.crm.private.example`; workspaces might
be `alpha.crm.private.example` and `beta.crm.private.example`. Do not use a raw
IP or set `SERVER_URL` to the login subdomain: Twenty adds the subdomain itself.

Before public activation, configure DNS and trusted HTTPS for the base domain
and `*.crm.private.example`, all forwarding to the **same** Twenty Service.
Use host Caddy as with Kaneo, preserving Host, cookies, redirects and WebSocket
upgrades; the upstream is Service `twenty`'s ClusterIP on port 3000. Host Caddy
can reach that Service without allowing arbitrary Pod ingress. Keep Redis and
Garage internal. Twenty proxies authenticated file access through its API;
there is no browser-facing S3 route or bucket CORS requirement.

Server-2's current Caddy uses a stock image and HTTP certificate challenges.
Wildcard certificates need a separately configured DNS challenge provider, or
explicit Caddy sites/certificates for every workspace hostname. A wildcard DNS
record alone does not provide TLS. Add routing only during activation, after
choosing that approach and validating Caddy; this dormant change adds no Caddy
environment dependency. For a private rehearsal, use local DNS/hosts and a
trusted proxy with the same hostname layout, rather than IP-based port-forward
URLs.

## Prepare inputs when ready to activate

Do not run this section merely to merge the dormant manifests. Real inputs stay
in the untracked `local/public-edge-inputs/twenty/` directory on the server;
keep an encrypted copy off-host. Never replace existing inputs with new keys.

1. Follow [PostgreSQL, Add an app](postgres.md#add-an-app), **password step only**,
   with `app=twenty`. This creates `twenty-db-password` in namespace `postgres`,
   labelled `cnpg.io/reload=true`. Do not add Twenty to the shared PostgreSQL
   Kustomization: its nested folder has a separate Flux owner.
2. Create the application credentials, reusing that hex-encoded password so it
   is safe in a PostgreSQL URL, and configure the base URL privately:

   ```bash
   set -euo pipefail
   umask 077
   dir=local/public-edge-inputs/twenty
   mkdir -p "$dir"
   if [ -e "$dir/credentials.env" ] || [ -e "$dir/settings.env" ]; then
     echo 'Inputs exist; inspect them, do not regenerate.' >&2
     test -s "$dir/credentials.env" && test -s "$dir/settings.env"
   else
     test -s "$dir/db-password"
     {
       printf 'PG_DATABASE_URL=postgres://twenty:%s@postgres-rw.postgres.svc.cluster.local:5432/twenty\n' "$(cat "$dir/db-password")"
       printf 'APP_SECRET=%s\nENCRYPTION_KEY=%s\n' "$(openssl rand -base64 32)" "$(openssl rand -base64 32)"
     } > "$dir/credentials.env"
     printf 'SERVER_URL=https://crm.private.example\n' > "$dir/settings.env"
   fi
   # Set the actual base domain in this private file before creating the ConfigMap.
   "${EDITOR:-nano}" "$dir/settings.env"
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/apps/twenty/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n twenty create secret generic twenty-credentials \
     --from-env-file="$dir/credentials.env"
   kubectl --context "$PUBLIC_CONTEXT" -n twenty create configmap twenty-settings \
     --from-env-file="$dir/settings.env"
   ```

   `APP_SECRET` and `ENCRYPTION_KEY` are distinct, shared by server and worker.
   Losing the encryption key loses access to stored integration secrets.
3. Follow [Garage, Add a bucket](garage.md#add-a-bucket-for-another-app) for
   `twenty-uploads`, with a **new Twenty-only key**, no bucket-creation rights
   and an initial 2 GiB quota. Capture newly generated key output directly into
   a mode-0600 private file instead of displaying it. Store `S3_ACCESS_KEY_ID`
   and `S3_SECRET_ACCESS_KEY` in `$dir/storage.env`; create the application Secret:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" -n twenty create secret generic twenty-storage \
     --from-env-file="$dir/storage.env"
   ```

   Never reuse Kaneo's key. The policies allow only Twenty's server/worker Pods
   to reach Garage on 3900, their Redis on 6379 and the internal server on 3000.
   `SERVER_INTERNAL_URL` keeps local logic-function API calls inside the cluster.
   External integrations and outbound HTTP/SMTP are blocked until their required
   egress is reviewed.
4. In Git, change `twenty-database.spec.suspend` to `false`. Merge/reconcile and
   wait for both `databaserole/twenty` and `database/twenty` in `postgres` to
   report `status.applied=true`. The operator installs `uuid-ossp`, `unaccent`
   and `citext` in Twenty's database. Its role gets no superuser, role-creation
   or cross-app grants.

## Start and accept the instance

Keep worker replicas at zero initially. Add `twenty-database` to Twenty's Flux
`dependsOn`, and change Redis and server replicas to 1 **in Git**. Flux overwrites
manual scaling. Allow up to 15 minutes for the server's initial migrations;
read its logs for migration/upgrade errors as well as checking `/healthz`.
Only the server runs migrations and registers recurring jobs. After it is
healthy, change worker replicas to 1 and reconcile again. Worker probes check
process identity only, so verify a background job below.
Activation also deliberately updates the dormant-state assertions in
`scripts/check-kubernetes.py`; retain the workspace configuration checks.

Claim the first workspace through a private route before opening public access.
Multi-workspace mode does not automatically grant instance admin access. Promote
only the operator's verified user UUID using a local PostgreSQL superuser
session (`kubectl exec` into the primary, as in the PostgreSQL guide):

```sql
UPDATE core."user" SET "canAccessFullAdminPanel" = true
WHERE id = '<operator-user-uuid>' RETURNING id;
```

Confirm exactly the intended user changed. Leave `canImpersonate` false.
The operator can then create the businesses' workspaces and invite their users.
The creation restriction prevents uninvited public signups once the first
workspace exists in this pinned release. Configure SMTP before relying on
invitation/reset email; it is not provisioned by these manifests.

Before inviting businesses, use two ordinary accounts in separate browser
profiles, each belonging to only one business, and complete these checks:

- Create a company/contact and upload an attachment in each workspace.
- Each account sees only its business's records and workspace memberships.
  Visiting the other workspace subdomain must not grant membership or data.
- With A's session/API key, request B's known record ID and attachment URL:
  access must be denied, including REST/GraphQL, search and exports. Repeat
  in the other direction. A UI-only check is insufficient.
- Run a background workflow/job, confirm it finishes for the correct workspace,
  then restart server, worker and Redis and verify records/files remain.
- Test login, workspace switching for an intentionally shared operator account,
  logout, invitations and HTTPS across both business subdomains.
- Restore an isolated recovery set and repeat representative reads/writes.

Offline manifest checks do not prove workspace separation, DNS/TLS or recovery.
A disposable Docker smoke test on 2026-11-18 completed initial migrations and
served `/healthz` on PostgreSQL 18.6 with a non-superuser role and read-only
application root filesystem; the worker also started and passed its process
probe. This did not exercise K3s/CNPG, Garage file operations or two-business
access controls. The two-business test above remains an activation gate.

## State, backups and shutdown

Durable business state lives in the shared PostgreSQL database and Garage bucket.
The existing K3s capture discovers declared databases and each app's
`storage.env`, so once provisioned it will capture `databases/twenty.dump`,
`objects/twenty-uploads/` and `inputs/twenty/` alongside the other apps. This
dormant branch provisions none of those live inputs and changes no backup job.
See [K3s backups](../backups.md#the-public-k3s-cluster-server-2).

Redis has retained, node-local AOF storage with `noeviction`; queue/cache state
is **not** in that capture. A node/disk loss can lose pending jobs, and retries
may duplicate external effects. Plan to inspect/requeue jobs when recovering;
do not promise exact workflow continuation. Temporary Pod paths are disposable.
For a coherent manual recovery set, stop server/worker writes, capture a native
database dump and the matching bucket, credentials/settings and encryption key,
then restore into a separate cluster before changing production.

To park Twenty after activation, remove its public route and set all three
replica counts to zero in Git. Retain the database, Garage bucket, Redis PVC
and private inputs; suspending Flux alone does not stop running Pods.

## Upstream references

- [Self-host configuration and multi-workspace setup](https://docs.twenty.com/developers/self-host/capabilities/setup)
- [Pinned v2.59.0 release](https://github.com/twentyhq/twenty/releases/tag/twenty/v2.59.0)
- [Server/worker startup and Redis contract](https://github.com/twentyhq/twenty/blob/twenty/v2.59.0/packages/twenty-docker/docker-compose.yml)
- [Domain routing implementation](https://github.com/twentyhq/twenty/blob/twenty/v2.59.0/packages/twenty-server/src/engine/core-modules/domain/domain-server-config/services/domain-server-config.service.ts)
- [Workspace schema naming](https://github.com/twentyhq/twenty/blob/twenty/v2.59.0/packages/twenty-server/src/engine/workspace-datasource/utils/get-workspace-schema-name.util.ts)
