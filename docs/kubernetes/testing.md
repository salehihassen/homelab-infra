# Test the public K3s cluster

Run these after [setup](bootstrap.md), on the public server, from the repo
root, with `KUBECONFIG` and `PUBLIC_CONTEXT` exported. Each step lists what
good looks like. Steps 4–8 briefly interrupt only the new app. Step 9 reboots
the host and briefly interrupts every Compose service too, so schedule it
accordingly.

Flux deploys `main` only: a local edit or an unmerged branch changes nothing
live.

```bash
k() { kubectl --context "$PUBLIC_CONTEXT" "$@"; }
```

## 1. Validate files (no cluster needed)

```bash
bash scripts/validate-kubernetes.sh
```

Expect zero invalid resources and `Kubernetes rules passed.` This checks files
only, not the live cluster.

## 2. Check what was deployed

```bash
flux --context "$PUBLIC_CONTEXT" get sources git
flux --context "$PUBLIC_CONTEXT" get kustomizations
k -n postgres get cluster,databaserole,database,pods,svc,pvc
primary="$(k -n postgres get pod -o name -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary)"
k -n postgres exec "$primary" -c postgres -- psql -tAc 'SHOW server_version; SELECT datname FROM pg_database;'
k -n kaneo get deployment,pods,svc
```

**Good looks like:**

- **Source:** revision `main@sha1:<latest merge>`.
- **Kustomizations:** `flux-system`, `cloudnative-pg`, `postgres` and each app
  Ready=True.
- **Postgres:** "Cluster in healthy state"; each app's role and database
  APPLIED=true; Pod `postgres-1` 1/1 Running; Services `postgres-rw`,
  `postgres-ro` and `postgres-r`; one PVC, Bound.
- **SQL:** PostgreSQL `18.6`, and the list includes `kaneo`.
- **Kaneo:** 1/1 Running, 0 restarts.

## 3. Open Kaneo and claim the admin account

The settings ConfigMap must start with `KANEO_CLIENT_URL=http://127.0.0.1:8911`.

```bash
k -n kaneo port-forward --address=127.0.0.1 service/kaneo 8911:5173 &
curl --fail http://127.0.0.1:8911/api/health
```

From a laptop, tunnel first: `ssh -N -L 8911:127.0.0.1:8911 <server-2>`. Open
`http://127.0.0.1:8911`. Use exactly `127.0.0.1`, not `localhost`: login
checks the address.

- [ ] Claim the admin account **before** any public route exists. The first
      sign-up always works, even with registration disabled.
- [ ] Create a test project and task.
- [ ] Log out; the project is no longer visible.
- [ ] A second sign-up is refused.

Stop the port-forward with `kill %1` when done.

## 4. Flux repairs drift

```bash
flux --context "$PUBLIC_CONTEXT" suspend kustomization kaneo
k -n kaneo scale deployment/kaneo --replicas=0
flux --context "$PUBLIC_CONTEXT" resume kustomization kaneo
k -n kaneo rollout status deployment/kaneo --timeout=300s
```

Expect Kaneo back at 1/1: Git says one replica. For permanent changes, edit
Git and merge.

## 5. A merged PR rolls out

Open a small PR (for example, change Kaneo's CPU request in
`kubernetes/apps/kaneo/deployment.yaml`), merge it after CI, then:

```bash
flux --context "$PUBLIC_CONTEXT" get sources git    # wait for the merge commit, ~1 min
k -n kaneo get pod -o jsonpath='{..resources.requests.cpu}{"\n"}'
```

Expect the merge commit's SHA and the new value. Kaneo is down for about
10–20 s while its Pod is replaced (`strategy: Recreate`). Mission Control shows
the same revision.

## 6. Data survives restarts

```bash
k -n postgres delete pod -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary
k -n postgres wait --for=condition=Ready cluster/postgres --timeout=300s
k -n kaneo rollout restart deployment/kaneo && k -n kaneo rollout status deployment/kaneo --timeout=300s
k -n postgres get pvc
```

Expect the same PVC, still Bound. Redo step 3's port-forward: the test task is
still there. This proves restarts keep data; it is not a backup test.

## 7. Network policies hold

Each probe waits 5 seconds first: policies take a moment to reach a new Pod.

```bash
probe() { k run "probe-$RANDOM" -q --rm -i --restart=Never --image=busybox:1.37 "${@:3}" --command -- \
  sh -c "sleep 5; nc -w3 $1 $2 </dev/null && echo OPEN || echo BLOCKED"; }
probe kubernetes.default.svc 443                                    # OPEN: control, the probe works
probe postgres-rw.postgres.svc.cluster.local 5432                   # BLOCKED: no client label
probe kaneo.kaneo.svc.cluster.local 5173                            # BLOCKED
probe postgres-rw.postgres.svc.cluster.local 5432 --labels=homelab.example/postgres-client=true   # OPEN
probe kaneo.kaneo.svc.cluster.local 5173 --labels=homelab.example/postgres-client=true            # BLOCKED
```

From inside Kaneo, only the database is reachable:

```bash
k -n kaneo exec deploy/kaneo -- node -e '
const net = require("net");
const t = (h, p) => new Promise(r => {
  const s = net.connect({host: h, port: p, timeout: 3000}, () => { s.destroy(); r("OPEN"); });
  s.on("error", () => r("BLOCKED"));
  s.on("timeout", () => { s.destroy(); r("BLOCKED"); });
});
(async () => {
  for (const [h, p] of [["postgres-rw.postgres.svc.cluster.local", 5432],
                        ["kubernetes.default.svc", 443], ["1.1.1.1", 443]])
    console.log(h, p, await t(h, p));
})();'
```

Expect `5432 OPEN`, then `443 BLOCKED` twice. Never add an allow-all policy to
make a probe pass. Check no probe Pods are left: `k get pods -A | grep probe`.

## 8. Nothing new is public

From **another machine** (not the server), against the server's public IP:

```bash
for p in 6443 10250 5432 5173 9080 9081 8911; do nc -zvw3 <server-2-public-ip> $p; done   # all must fail
```

On the server, compare listeners with the setup baseline:
`sudo ss -lntup | diff /tmp/ss-before.txt -`. New listeners should be K3s's
own, on loopback or the cluster ranges, and Caddy's tailnet-only 9081. Open
every existing public site and check it still works.

## 9. Survives a reboot

This takes down every service on the host, Compose and public Caddy included,
until it is back up.

```bash
sudo reboot
# reconnect, wait ~2 minutes
flux --context "$PUBLIC_CONTEXT" get kustomizations   # all Ready
k get pods -A                                         # all Running
free -m; docker ps --format '{{.Names}}\t{{.Status}}' | grep -v ' Up ' || echo "all containers Up"
sudo dmesg | grep -i oom                              # no output
```

Then repeat step 3: the test task is still there.

## 10. Keep an eye on resources

Over the next days:

```bash
free -m; df -h /
k get events -A | grep -iE 'oom|evict|pressure'
```

`vmstat 5` columns `si`/`so` are KiB/s: tens are fine, thousands mean memory
pressure. Disk pressure evicts **every** Pod, so keep at least 15% of the disk
free.

## Then: backups, then public HTTPS

1. Do the backup and restore drill in [kaneo.md](kaneo.md#capture-one-coherent-recovery-set).
2. Add the public route ([kaneo.md](kaneo.md#add-the-public-https-route)).
   Check from outside: valid certificate, login, project/task CRUD,
   `/api/health` 200, and every old public site unchanged.

If the new route fails, remove only that route and troubleshoot through the
port-forward. Leave Compose and Kaneo's data alone.

## If something is stuck

```bash
flux --context "$PUBLIC_CONTEXT" get all
k -n postgres get events --sort-by=.lastTimestamp | tail -20
k -n cnpg-system logs deployment/cnpg-controller-manager --tail=50
k -n postgres describe databaserole kaneo | tail -15
k -n kaneo describe pods | tail -30
k -n kaneo logs deployment/kaneo --tail=100
k describe node | grep -A5 Taints     # disk-pressure / memory-pressure
```

| Symptom | Usual cause |
| --- | --- |
| Source not Ready, "Source artifact not found" | Deploy key not added on GitHub, or wrong URL |
| Postgres Pod Pending | The node isn't labelled `homelab.example/local-storage=true` |
| Role not applied | `<app>-db-password` missing, or not the `basic-auth` type |
| Kaneo `CreateContainerConfigError` | A Secret or ConfigMap key is missing |
| Every Pod Evicted, node tainted `disk-pressure` | Disk nearly full: free space (caches, unused images), then wait |
| Login fails | The browser address doesn't match `KANEO_CLIENT_URL` exactly |

Logs and events can contain private details: redact them before sharing.
**Never delete a PVC to fix a startup problem.**
