# Shared public object storage (Garage)

The public cluster runs one [Garage](https://garagehq.deuxfleurs.fr) node, an
S3-compatible object store, for apps that store files. Each app gets its own
bucket and key, and a key reaches only its own bucket. Today the only bucket
is `kaneo-uploads`, for Kaneo's attachments.

| Git path | What it holds |
|---|---|
| `kubernetes/storage/garage/statefulset.yaml` | Garage 2.5.0, pinned by digest: Pod `garage-0`, 10 GiB on `local-retain` on the local-storage node |
| `kubernetes/storage/garage/garage.toml` | One node, one copy, SQLite metadata, every write synced to disk |
| `kubernetes/storage/garage/service.yaml` | ClusterIP Service `garage`, the S3 API on 3900 |
| `kubernetes/storage/garage/network-policy.yaml` | Default deny: no Pod connects to Garage |

Flux applies Kustomization `garage` on its own; `kaneo` waits for it and for
`postgres`. On first start, `--single-node --default-bucket` sets up the node
and creates `kaneo-uploads` with the key in Secret `garage-credentials`. Later
starts find them and change nothing, except that the key gets owner
permission on its bucket again.

**How Kaneo uses it:** Compose Caddy on the host forwards
`https://<kaneo domain>/kaneo-uploads/*` to Garage's ClusterIP (the Kaneo site
in `hosts/server-2/caddy/Caddyfile`). Browsers upload straight there with
short-lived presigned URLs from Kaneo, on Kaneo's own origin, so there is no
CORS to configure. Kaneo itself uses the same address, through Caddy, so it
needs outbound HTTPS rather than a path to Garage. Downloads go through Kaneo,
which checks who may see them. Garage refuses unsigned requests.

The data lives on the local-storage node's disk, like PostgreSQL's. The volume
size is not a quota; the bucket quota below is.

## First-time setup

During [setup](bootstrap.md) step 5, or, on a running cluster, before Flux
first applies `kubernetes/storage/garage`.

1. **Credentials:** an RPC secret and Kaneo's key pair. Each app's
   `storage.env` is also how the nightly backup finds the buckets to copy.

   ```bash
   umask 077
   mkdir -p local/public-edge-inputs/garage local/public-edge-inputs/kaneo
   garage_env=local/public-edge-inputs/garage/credentials.env
   kaneo_env=local/public-edge-inputs/kaneo/storage.env
   if [ -e "$garage_env" ]; then
     echo "Inputs exist; inspect them, do not regenerate." >&2
   else
     key_id="GK$(openssl rand -hex 12)" secret="$(openssl rand -hex 32)"
     printf 'GARAGE_RPC_SECRET=%s\nGARAGE_DEFAULT_ACCESS_KEY=%s\nGARAGE_DEFAULT_SECRET_KEY=%s\n' \
       "$(openssl rand -hex 32)" "$key_id" "$secret" > "$garage_env"
     printf 'S3_ACCESS_KEY_ID=%s\nS3_SECRET_ACCESS_KEY=%s\n' "$key_id" "$secret" > "$kaneo_env"
     unset key_id secret
   fi
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/storage/garage/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n garage create secret generic garage-credentials \
     --from-env-file="$garage_env"
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/apps/kaneo/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create secret generic kaneo-storage \
     --from-env-file="$kaneo_env"
   ```

2. **Check** the key names, without printing values:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" -n garage get secret garage-credentials \
     -o go-template='{{range $k, $v := .data}}{{$k}} {{end}}{{"\n"}}'   # GARAGE_DEFAULT_ACCESS_KEY GARAGE_DEFAULT_SECRET_KEY GARAGE_RPC_SECRET
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo get secret kaneo-storage \
     -o go-template='{{range $k, $v := .data}}{{$k}} {{end}}{{"\n"}}'   # S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY
   ```

3. **Once Garage runs**, stop Kaneo's key from creating buckets (each would
   sit outside any quota) and cap its bucket. Both are stored in Garage and
   survive restarts:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" -n garage rollout status statefulset/garage --timeout=300s
   g() { kubectl --context "$PUBLIC_CONTEXT" -n garage exec garage-0 -- /garage "$@"; }
   key_id="$(sed -n 's/^S3_ACCESS_KEY_ID=//p' "$kaneo_env")"
   g key deny --create-bucket "$key_id"
   g bucket set-quotas kaneo-uploads --max-size 2GiB
   g key info "$key_id" | grep 'Can create buckets'   # false
   g bucket info kaneo-uploads                         # quota 2.0 GiB; that key alone, RWO
   ```

   Garage's data and the nightly backup's copy of it share the root disk,
   where K3s evicts Pods below 10% free. Raise the quota only with more disk.

4. **Copy `local/public-edge-inputs/` off the server.**

Don't edit the env files in an editor: values must not end in a newline.

## Add a bucket for another app

Never grant an existing key a new bucket. Create the bucket, then a key for
the app alone:

```bash
g bucket create <bucket>
g key create <app>                  # shows the secret once
g key deny --create-bucket <app>
g bucket allow --read --write <bucket> --key <app>
g bucket set-quotas <bucket> --max-size <size>
```

Write the key's ID and secret to `local/public-edge-inputs/<app>/storage.env`
as `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`, which also adds the bucket
to the nightly backup, and create the app's Secret from that file. If browsers
upload directly, the bucket also needs a Caddy route on the app's origin.

## Backups and recovery

Every night, server-2's backup copies each bucket an app's `storage.env` key
can read into `objects/<bucket>/` of the K3s capture (see
[backups.md](../backups.md#the-public-k3s-cluster-server-2)), after the
database dumps, so each dump's attachments are there.

If Garage's volume is lost, the apps stay up but their files are gone until
restored:

1. Recreate `garage-credentials` from the recovered
   `inputs/garage/credentials.env` (step 1 above, with that file). Flux starts
   Garage, which creates an empty `kaneo-uploads` and its key. Redo step 3.
2. Copy the objects back, with rclone (on server-2, the backup's own:
   `rclone_binary` in `/opt/infra-backup/tool.json`). The key goes through the
   environment, not the command line:

   ```bash
   src=<restore target>/opt/k3s-backup/latest
   kubectl --context "$PUBLIC_CONTEXT" -n garage port-forward --address=127.0.0.1 service/garage 3900:3900 &
   (
     set -a; . "$src/inputs/kaneo/storage.env"; set +a
     export RCLONE_CONFIG_GARAGE_TYPE=s3 RCLONE_CONFIG_GARAGE_PROVIDER=Other \
       RCLONE_CONFIG_GARAGE_ENDPOINT=http://127.0.0.1:3900 RCLONE_CONFIG_GARAGE_REGION=garage \
       RCLONE_CONFIG_GARAGE_FORCE_PATH_STYLE=true \
       RCLONE_CONFIG_GARAGE_ACCESS_KEY_ID="$S3_ACCESS_KEY_ID" \
       RCLONE_CONFIG_GARAGE_SECRET_ACCESS_KEY="$S3_SECRET_ACCESS_KEY"
     rclone --config /dev/null copy "$src/objects/kaneo-uploads" garage:kaneo-uploads
     rclone --config /dev/null check "$src/objects/kaneo-uploads" garage:kaneo-uploads   # 0 differences
   )
   kill %1
   ```

Never delete Garage's PVC to fix a startup problem: it holds the only live
copy.

## Upgrades

Change the tag and digest in `statefulset.yaml` by hand, after reading
Garage's release notes for breaking changes. With one Pod, a restart briefly
fails uploads and attachment downloads; the rest of Kaneo keeps working.
