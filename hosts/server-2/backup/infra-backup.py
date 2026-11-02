#!/usr/bin/env python3
"""Per-stack consistent backups; private settings are outside the checkout."""
import argparse
import datetime
import fcntl
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

import yaml

sys.excepthook = sys.__excepthook__
os.umask(0o077)
SYSTEM = ("/var/run/docker.sock", "/etc/machine-id", "/var/log/journal", "/run/log/journal")


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(args):
    return subprocess.check_output(args, text=True)


def under(path, roots):
    p = Path(path)
    return any(p == Path(r) or Path(r) in p.parents for r in roots)


def event(kind, **fields):
    print(json.dumps({"event": kind, "time": datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields}), flush=True)


def load(config):
    root = Path(config["infra_dir"]) / "hosts/server-2"
    model = json.loads(output(["docker", "compose", "--profile", "*", "config", "--no-env-resolution", "--format", "json"]) if Path.cwd() == root else subprocess.check_output(["docker", "compose", "--profile", "*", "config", "--no-env-resolution", "--format", "json"], cwd=root, text=True))
    declarations = {p.parent.name: yaml.safe_load(p.read_text()) or {} for p in root.glob("*/backup.yaml")}
    declarations["host"] = yaml.safe_load((root / "backup/host.yaml").read_text()) or {}
    local = root / "backup/host.local.yaml"
    declarations["host.local"] = yaml.safe_load(local.read_text()) or {} if local.exists() else {}
    allowed = {"paths", "volumes", "exclude", "sqlite", "sqlite_scan", "views"}
    for name, doc in declarations.items():
        if set(doc) - allowed:
            raise RuntimeError(name + ": unsupported backup declaration keys")
    running = json.loads(output(["docker", "inspect", *output(["docker", "ps", "-q"]).split()]))
    by_name = {c["Name"].lstrip("/"): c for c in running}
    volumes = {v["Name"]: v for v in json.loads(output(["docker", "volume", "inspect", *output(["docker", "volume", "ls", "-q"]).split()]))}
    all_paths = [p for d in declarations.values() for p in d.get("paths", [])]
    excluded = [p for d in declarations.values() for p in d.get("exclude", {})]
    declared_volumes = {v for d in declarations.values() for v in d.get("volumes", [])}
    groups = {}
    for source in root.glob("*/compose.yaml"):
        stack = source.parent.name
        names = list((yaml.safe_load(source.read_text()) or {}).get("services", {}))
        containers = []
        for name in names:
            s = model["services"][name]
            actual = s.get("container_name", name)
            if actual not in by_name:
                raise RuntimeError("Expected active service is not running: " + name)
            # AIO explicitly refuses digest references in its startup script.
            # Preserve its supported channel tag; snapshots record the image ID.
            if name != "nextcloud-aio-mastercontainer" and "@sha256:" not in s["image"] and not s["image"].startswith("sha256:"):
                raise RuntimeError("Runtime image must be digest/ID pinned: " + name)
            containers.append(by_name[actual])
        if stack == "nextcloud-aio":
            containers = [c for c in running if c["Name"].startswith("/nextcloud-aio-")]
        paths = set(declarations[stack].get("paths", []))
        mounts = {}
        for c in containers:
            for m in c["Mounts"]:
                if m["Source"] in SYSTEM:
                    continue
                if m["Type"] == "volume":
                    if m["Name"] not in declared_volumes:
                        raise RuntimeError("Undeclared active volume: " + m["Name"])
                    if m["Name"] == "nextcloud_aio_database":
                        continue  # Always replaced by native dumps.
                    paths.add(m["Source"])
                    mounts[m["Source"]] = {"kind": "volume", "name": m["Name"]}
                elif m["Type"] == "bind":
                    if under(m["Source"], excluded):
                        continue
                    if not under(m["Source"], all_paths):
                        raise RuntimeError("Undeclared active bind: " + m["Source"])
                    paths.add(m["Source"])
                    mounts[m["Source"]] = {"kind": "bind"}
        for name in names:
            s = model["services"][name]
            for e in s.get("env_file", []):
                path = e["path"] if isinstance(e, dict) else e
                if not under(path, all_paths):
                    raise RuntimeError("Undeclared runtime environment: " + path)
            context = s.get("build", {}).get("context")
            if context:
                if not under(context, all_paths):
                    raise RuntimeError("Undeclared application source: " + context)
                paths.add(context)
        groups[stack] = {"paths": sorted(paths), "mounts": mounts, "containers": containers, "exclude": excluded}
    covered = [p for g in groups.values() for p in g["paths"]]
    remaining = [p for p in declarations["host.local"].get("paths", []) if not under(p, covered)]
    groups["host"] = {"paths": sorted(set(declarations["host"].get("paths", []) + remaining + [str(root.parent.parent)])), "mounts": {}, "containers": [], "exclude": excluded}
    for group in groups.values():
        for path in group["paths"]:
            if not path.startswith("/"):
                raise RuntimeError("Backup path must be absolute: " + path)
    return root, model, groups


HELPER = r'''
import os,pathlib,json,sqlite3,shutil,tempfile,subprocess,hashlib
os.umask(0o077)
group=json.loads(GROUP)
config=json.loads(CONFIG)
stage=pathlib.Path(STAGE)
exports=[]
excludes=list(group['exclude'])
for source in group['paths']:
    root=pathlib.Path(source)
    if not root.exists():raise RuntimeError('Missing declared path: '+source)
    if any(root==pathlib.Path(e) or pathlib.Path(e) in root.parents for e in excludes):continue
    for base,dirs,files in os.walk(root):
        dirs[:]=[d for d in dirs if not any(pathlib.Path(base,d)==pathlib.Path(e) for e in excludes)]
        for name in files:
            p=pathlib.Path(base)/name
            if p.is_symlink() or not p.is_file() or p.stat().st_size<16:continue
            with p.open('rb') as f:magic=f.read(16)
            if magic!=b'SQLite format 3\0':continue
            target=stage/'database-files'/p.relative_to('/')
            target.parent.mkdir(parents=True,exist_ok=True)
            st=p.stat()
            with tempfile.TemporaryDirectory(dir='/tmp') as work:
                local=pathlib.Path(work)/p.name;shutil.copyfile(p,local)
                for suffix in ['-wal','-shm','-journal']:
                    side=pathlib.Path(str(p)+suffix)
                    if side.exists():shutil.copyfile(side,str(local)+suffix)
                a=sqlite3.connect(local);b=sqlite3.connect(target);a.backup(b)
                assert b.execute('PRAGMA integrity_check').fetchall()==[('ok',)],str(p)
                tables=[r[0] for r in b.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                counts={t:b.execute('SELECT count(*) FROM "'+t.replace('"','""')+'"').fetchone()[0] for t in tables}
                a.close();b.close()
            os.chown(target,st.st_uid,st.st_gid);os.chmod(target,st.st_mode&0o777)
            excludes.extend(str(p)+s for s in ['', '-wal','-shm','-journal'])
            exports.append({'source':str(p),'export':str(target),'uid':st.st_uid,'gid':st.st_gid,'mode':st.st_mode&0o777,'table_counts':counts})
inventory=stage/'sqlite.json';inventory.write_text(json.dumps(exports,indent=2));os.chown(inventory,1000,1000)
repo=pathlib.Path(config['repository_file']).read_text().strip()
env={**os.environ,'RESTIC_REPOSITORY':repo,'RESTIC_PASSWORD_FILE':config['password_file'],'RESTIC_CACERT':config['ca_file'],'RESTIC_CACHE_DIR':config['state_dir']+'/cache','GOMEMLIMIT':'320MiB'}
argv=['restic','backup','--json','--quiet','--host',config['host'],'--tag','stack:'+STACK]
for e in excludes:
    if e==config['state_dir']:
        continue  # Only the explicitly mounted current stage/cache are visible.
    argv+=['--exclude',e]
for record in exports:
    for suffix in ['', '-wal','-shm','-journal']:argv+=['--exclude',record['source']+suffix]
argv+=group['paths']+[str(stage)]
result=subprocess.run(argv,env=env,capture_output=True,text=True)
secret=repo.split('@',1)[0].split('://',1)[-1].split(':',1)[-1]
for value in [result.stdout,result.stderr]:
    if value:print(value.replace(repo,'[private-repository]').replace(secret,'[redacted]'),flush=True)
if result.returncode:raise RuntimeError('Restic backup failed: '+str(result.returncode))
receipt=stage/'receipt.json';receipt.write_text(result.stdout);os.chown(receipt,1000,1000)
print(json.dumps({'sqlite_exports':len(exports),'result':'passed'}),flush=True)
'''


def helper(config, group, stack, stage):
    args = ["docker", "run", "--rm", "-i", "--name", "server-2-backup-tool", "--network", "bridge", "--read-only", "--memory", "512m", "--cpus", "1.5", "--tmpfs", "/tmp:rw,size=128m", "--mount", "type=bind,src=" + config["restic_binary"] + ",dst=/usr/local/bin/restic,readonly", "--mount", "type=bind,src=/opt/infra-backup,dst=/opt/infra-backup,readonly", "--mount", "type=bind,src=" + str(stage) + ",dst=" + str(stage), "--mount", "type=bind,src=" + config["state_dir"] + "/cache,dst=" + config["state_dir"] + "/cache"]
    seen = {"/opt/infra-backup", str(stage), config["state_dir"] + "/cache"}
    for path in group["paths"]:
        if path in seen or under(path, [config["state_dir"]]):
            continue
        m = group["mounts"].get(path, {})
        args += ["--mount", "type=" + ("volume,src=" + m["name"] if m.get("kind") == "volume" else "bind,src=" + path) + ",dst=" + path + ",readonly"]
        seen.add(path)
    script = "GROUP=" + repr(json.dumps(group)) + "\nCONFIG=" + repr(json.dumps(config)) + "\nSTAGE=" + repr(str(stage)) + "\nSTACK=" + repr(stack) + "\n" + HELPER
    args += ["--entrypoint", "python", config["tool_image"], "-"]
    command(args, input=script, text=True)


def dump(stage, filename, container, program):
    with (stage / filename).open("wb") as f:
        command(["docker", "exec", container, "sh", "-c", program], stdout=f)


def nextcloud_exports(stage):
    name = "nextcloud-aio-database"
    dump(stage, "database.dump", name, 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"')
    dump(stage, "globals.sql", name, 'exec pg_dumpall --globals-only -U "$POSTGRES_USER"')
    with (stage / "database.dump").open("rb") as src:
        command(["docker", "exec", "-i", name, "pg_restore", "--list"], stdin=src, stdout=subprocess.DEVNULL)
    sql = "SELECT format('ALTER DATABASE %I OWNER TO %I;',datname,pg_get_userbyid(datdba)) FROM pg_database WHERE datname=current_database(); SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC;',current_database()); SELECT format('GRANT %s ON DATABASE %I TO %s%s;',x.privilege_type,d.datname,CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(x.grantee)) END,CASE WHEN x.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END) FROM pg_database d CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) x WHERE d.datname=current_database();"
    with (stage / "database-attributes.sql").open("wb") as f:
        command(["docker", "exec", name, "sh", "-c", 'exec psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', "sh", sql], stdout=f)
    def query(statement):
        return output(["docker", "exec", name, "sh", "-c", 'exec psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', "sh", statement])
    tables = query("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
    counts = " UNION ALL ".join("SELECT '" + t.replace("'", "''") + "',count(*) FROM \"" + t.replace('"', '""') + '\"' for t in tables) + " ORDER BY 1"
    (stage / "postgres-counts.sql").write_text(counts)
    (stage / "postgres-counts.txt").write_text(query(counts))
    command(["docker", "exec", "nextcloud-aio-redis", "sh", "-c", 'REDISCLI_AUTH="$REDIS_HOST_PASSWORD" redis-cli SAVE'], stdout=subprocess.DEVNULL)


def backup_stack(config, name, group):
    stage = Path(config["state_dir"]) / "staging" / name
    if stage.exists():
        raise RuntimeError("Inspect stale staging before retrying: " + str(stage))
    stage.mkdir(parents=True, mode=0o700)
    (stage / "inventory.json").write_text(json.dumps(group, indent=2))
    if group["containers"]:
        identities = sorted({c["Image"] for c in group["containers"]})
        (stage / "images.json").write_text(output(["docker", "image", "inspect", *identities]))
    stopped = []
    maintenance = False
    try:
        if name == "nextcloud-aio":
            status = json.loads(output(["docker", "exec", "--user", "www-data", "nextcloud-aio-nextcloud", "php", "occ", "status", "--output=json"]))
            if status.get("maintenance"):
                raise RuntimeError("Preserving existing Nextcloud maintenance state; backup aborted")
            command(["docker", "exec", "--user", "www-data", "nextcloud-aio-nextcloud", "php", "occ", "maintenance:mode", "--on"], stdout=subprocess.DEVNULL)
            maintenance = True
        quiesce = group["containers"] if name in {"nextcloud-aio", "flowise", "n8n", "open-webui"} else []
        for c in quiesce:
            container = c["Name"].lstrip("/")
            if container in {"nextcloud-aio-database", "nextcloud-aio-redis"}:
                continue
            stopped.append(container)
            command(["docker", "stop", "--time", "60", container], stdout=subprocess.DEVNULL)
        if name == "nextcloud-aio":
            nextcloud_exports(stage)
        if name in {"mock-traveler", "personal-network"}:
            image = group["containers"][0]["Image"]
            dumpfile = stage / "local-image.tar"
            with dumpfile.open("wb") as f:
                command(["docker", "image", "save", image], stdout=f)
        helper(config, group, name, stage)
        event("stack_backup_complete", stack=name)
    finally:
        errors = []
        for container in reversed(stopped):
            try:
                command(["docker", "start", container], stdout=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                errors.append(container)
        if maintenance:
            for attempt in range(90):
                result = subprocess.run(["docker", "exec", "--user", "www-data", "nextcloud-aio-nextcloud", "php", "occ", "maintenance:mode", "--off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode == 0:
                    maintenance = False
                    break
                time.sleep(2)
            if maintenance:
                errors.append("Nextcloud maintenance cleanup")
        if errors:
            raise RuntimeError("Recovery cleanup needs attention: " + ", ".join(errors))
    # This stage is only temporary; SQLite files may retain service ownership.
    command(["docker", "run", "--rm", "--network", "none", "--mount", "type=bind,src=" + str(stage.parent) + ",dst=/staging", "--entrypoint", "python", config["tool_image"], "-c", "import pathlib,shutil;p=pathlib.Path('/staging')/" + repr(name) + ";assert p.parent==pathlib.Path('/staging');shutil.rmtree(p)"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["check", "backup"])
    parser.add_argument("--stack")
    args = parser.parse_args()
    config = json.loads(Path(os.environ.get("BACKUP_CONFIG", "/opt/infra-backup/config.json")).read_text())
    state = Path(config["state_dir"])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    (state / "cache").mkdir(exist_ok=True, mode=0o700)
    with (state / "backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            _, _, groups = load(config)
            if args.stack and args.stack not in groups:
                raise RuntimeError("Unknown stack requested: " + args.stack)
            event("coverage_check_passed", stacks=sorted(groups))
            if args.operation == "check":
                return
            (state / "status.json").write_text(json.dumps({"result": "running", "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
            for name, group in groups.items():
                if not args.stack or args.stack == name:
                    event("stack_backup_started", stack=name)
                    backup_stack(config, name, group)
            status = {"result": "success", "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "stack": args.stack or "all"}
            (state / "status.json").write_text(json.dumps(status))
            event("backup_complete", **status)
        except BaseException as error:
            (state / "status.json").write_text(json.dumps({"result": "failed", "error_type": type(error).__name__}))
            event("backup_failed", error_type=type(error).__name__)
            raise


if __name__ == "__main__":
    def interrupt(signum, frame):
        subprocess.run(["docker", "rm", "-f", "server-2-backup-tool"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise InterruptedError("Backup interrupted; application cleanup will run")

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupt)
    main()
