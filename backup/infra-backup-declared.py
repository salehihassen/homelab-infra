#!/usr/bin/env python3
"""Turn a host's backup declarations into a checked plan and consistent database dumps.

Declarations: <infra>/hosts/<host>/backup/host.yaml, the optional untracked
host.local.yaml beside it (personal paths kept out of Git), and one
backup.yaml per stack directory (every directory with a compose.yaml).
Format: docs/backups.md.

Subcommands:
  check [--live]        Validate declarations and require every bind mount,
                        env_file and secret file in the rendered Compose project
                        to be backed up or explicitly excluded. --live also
                        requires every database in the shared PostgreSQL
                        cluster to be declared by some stack.
  plan STAGE            check --live, then write STAGE/plan.json and
                        STAGE/excludes.txt for the restic runner.
  dump-postgres STAGE   Dump globals once and each declared database with
                        pg_dump; pause quiesce groups around their dumps and
                        copy their files into STAGE/quiesced. Paused containers
                        are always resumed.
"""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

import yaml

# Defaults: this checkout and the host directory set by INFRA_HOST.
DEFAULT_INFRA = str(Path(__file__).resolve().parents[1])
KEYS = {"paths", "volumes", "adapter", "save_images", "exclude", "exclude_patterns", "sqlite", "sqlite_scan", "postgres", "quiesce", "offline", "views"}
# Host files mounted for runtime only; nothing to restore.
SYSTEM_SOURCES = ("/etc/localtime", "/var/run/docker.sock", "/run/", "/dev/", "/sys/", "/proc/")
POSTGRES_CONTAINER = "postgres"


def fail(message):
    print(f"backup declarations: {message}", file=sys.stderr)
    sys.exit(1)


def under(path, roots):
    path = os.path.normpath(path)
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in map(os.path.normpath, roots))


def copy_roots(paths):
    roots = []
    for path in sorted(set(map(os.path.normpath, paths)), key=len):
        if not under(path, roots):
            roots.append(path)
    return sorted(roots)


def load(infra, host):
    host_dir = Path(infra) / "hosts" / host
    declarations = {}
    files = [("host", host_dir / "backup" / "host.yaml")]
    if (host_dir / "backup" / "host.local.yaml").is_file():
        files.append(("host.local", host_dir / "backup" / "host.local.yaml"))
    files += [(p.parent.name, p.parent / "backup.yaml") for p in sorted(host_dir.glob("*/compose.yaml"))]
    for name, path in files:
        if not path.is_file():
            fail(f"{name}: missing {path}")
        doc = yaml.safe_load(path.read_text()) or {}
        unknown = set(doc) - KEYS
        if unknown:
            fail(f"{name}: unknown keys {sorted(unknown)} in {path}")
        if name not in ("host", "host.local") and "exclude_patterns" in doc:
            fail(f"{name}: exclude_patterns is host-level only")
        for key in ("paths", "volumes", "save_images", "sqlite", "sqlite_scan", "postgres", "offline", "exclude_patterns", "views"):
            value = doc.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                fail(f"{name}: {key} must be a list of strings")
        exclude = doc.get("exclude", {})
        if not isinstance(exclude, dict) or not all(isinstance(v, str) and v for v in exclude.values()):
            fail(f"{name}: exclude must map each path to a reason")
        quiesce = doc.get("quiesce")
        if quiesce is not None:
            if set(quiesce) - {"services", "copy", "method"} or not quiesce.get("services"):
                fail(f"{name}: quiesce needs `services` and optionally `copy`")
            if quiesce.get("method", "pause") not in {"pause", "stop"}:
                fail(f"{name}: invalid quiesce method")
            if not doc.get("postgres") and not quiesce.get("copy") and not doc.get("volumes") and not doc.get("adapter"):
                fail(f"{name}: quiesce has nothing to make consistent")
        for path in doc.get("paths", []) + list(exclude) + doc.get("sqlite", []) + doc.get("offline", []):
            if not path.startswith("/"):
                fail(f"{name}: path must be absolute: {path}")
        declarations[name] = doc
        if doc.get("adapter") not in (None, "nextcloud-aio"):
            fail(f"{name}: unsupported capture adapter")
    return host_dir, declarations


def merged(declarations, key):
    out = []
    for doc in declarations.values():
        out.extend(doc.get(key, []))
    return out


def compose_sources(host_dir):
    """Every host path the rendered project reads: bind mounts, env_files, secret files.

    Values are the services using each path; read-only bind mounts are marked
    so `views` can be limited to them."""
    config = json.loads(subprocess.check_output(
        ["docker", "compose", "--profile", "*", "config", "--format", "json"], cwd=host_dir, text=True))
    sources = {}
    for name, service in config["services"].items():
        for volume in service.get("volumes", []):
            if volume.get("type") == "bind":
                sources.setdefault(volume["source"], set()).add(name + (":ro" if volume.get("read_only") else ""))
            elif volume.get("type") == "volume" and volume.get("source"):
                definition = config.get("volumes", {}).get(volume["source"], {})
                sources.setdefault("volume:" + definition.get("name", volume["source"]), set()).add(name)
        for env_file in service.get("env_file", []):
            sources.setdefault(env_file["path"] if isinstance(env_file, dict) else env_file, set()).add(name)
    for secret in (config.get("secrets") or {}).values():
        if secret.get("file"):
            sources.setdefault(secret["file"], {"(secret)"})
    return sources


def postgres_databases():
    query = "select datname from pg_database where not datistemplate order by 1"
    out = subprocess.check_output(
        ["docker", "exec", POSTGRES_CONTAINER, "sh", "-ec",
         f'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "{query}"'], text=True)
    return set(out.split())


def check(infra, host, live):
    host_dir, declarations = load(infra, host)
    covered = merged(declarations, "paths") + merged(declarations, "offline")
    covered += [p for doc in declarations.values() for p in (doc.get("quiesce") or {}).get("copy", [])]
    excluded = [p for doc in declarations.values() for p in doc.get("exclude", {})]
    views = merged(declarations, "views")
    problems = []
    for source, users in sorted(compose_sources(host_dir).items()):
        if source in views:
            if not all(user.endswith(":ro") for user in users):
                problems.append(f"view {source} must be mounted read-only ({', '.join(sorted(users))})")
            continue
        if source.startswith("volume:"):
            if source[7:] not in merged(declarations, "volumes"):
                problems.append(f"named volume {source[7:]} ({', '.join(sorted(users))}) needs a declaration")
        elif under(source, SYSTEM_SOURCES) or source.startswith(SYSTEM_SOURCES):
            continue
        elif not (under(source, covered) or under(source, excluded)):
            problems.append(f"{source} ({', '.join(sorted(users))}) is neither backed up nor excluded")
    for path in merged(declarations, "sqlite"):
        if not under(path, covered):
            problems.append(f"SQLite file {path} is outside every declared path")
    owners = {}
    for name, doc in declarations.items():
        for database in doc.get("postgres", []):
            if database in owners:
                problems.append(f"database {database} declared by both {owners[database]} and {name}")
            owners[database] = name
    if live and owners:
        for database in sorted(postgres_databases() - set(owners)):
            problems.append(f"PostgreSQL database {database} is not declared by any stack")
        for database in sorted(set(owners) - postgres_databases()):
            problems.append(f"declared PostgreSQL database {database} ({owners[database]}) does not exist")
    for problem in problems:
        print(f"backup coverage: {problem}", file=sys.stderr)
    if problems:
        sys.exit(1)
    if live and any(d.get("adapter") == "nextcloud-aio" for d in declarations.values()):
        import nextcloud
        nextcloud.inspect(declarations)
    return host_dir, declarations


def plan(infra, host, stage):
    host_dir, declarations = check(infra, host, live=True)
    stage = Path(stage)
    services = json.loads(subprocess.check_output(
        ["docker", "compose", "--profile", "*", "config", "--format", "json"],
        cwd=host_dir, text=True))["services"]
    built_images = {service["image"] for service in services.values() if service.get("build")}
    volume_names = merged(declarations, "volumes")
    volumes = json.loads(subprocess.check_output(["docker", "volume", "inspect", *volume_names], text=True)) if volume_names else []
    if any(v["Driver"] != "local" for v in volumes):
        fail("declared volumes must use the local driver")
    volume_paths = {v["Name"]: v["Mountpoint"] for v in volumes}
    for doc in declarations.values():
        # Mutable volume contents need a coordinated copy; Nextcloud's adapter
        # supplies its native-database exclusions and child-container inventory.
        if doc.get("quiesce"):
            doc["quiesce"].setdefault("copy", [])
            doc["quiesce"]["copy"] += [volume_paths[n] for n in doc.get("volumes", [])]
            if doc["quiesce"].get("method") == "stop":
                for name in doc["quiesce"]["services"]:
                    doc["quiesce"]["copy"] += [m["source"] for m in services.get(name, {}).get("volumes", [])
                                               if m["type"] == "bind" and not m.get("read_only")]
    adapters = {}
    if any(d.get("adapter") == "nextcloud-aio" for d in declarations.values()):
        import nextcloud
        adapters = nextcloud.plan(declarations, volume_paths)
    for doc in declarations.values():
        if doc.get("quiesce"):
            doc["quiesce"]["copy"] = copy_roots(doc["quiesce"]["copy"])
    quiesced_copies = [p for doc in declarations.values() for p in (doc.get("quiesce") or {}).get("copy", [])]
    excludes = [p for doc in declarations.values() for p in doc.get("exclude", {})]
    # Live files replaced by staged consistent copies.
    excludes += quiesced_copies
    excludes += [p + suffix for p in merged(declarations, "sqlite") for suffix in ("", "-wal", "-shm", "-journal")]
    excludes += merged(declarations, "exclude_patterns")
    # State/cache is excluded as a live source. Its explicitly selected staging
    # directory must still be included in the snapshot.
    excludes = [p for p in excludes if p != os.environ.get("BACKUP_STATE_DIR")]
    result = {
        "sources": sorted(set(merged(declarations, "paths"))),
        "sqlite": sorted(set(merged(declarations, "sqlite"))),
        "sqlite_scan": sorted(set(merged(declarations, "sqlite_scan"))),
        "offline": sorted(set(merged(declarations, "offline"))),
        "views": sorted(set(merged(declarations, "views"))),
        # Only explicit path exclusions, not live DB files replaced by exports
        # or restic globs. Native exporters use these to respect experiments.
        "excluded_paths": sorted({p for doc in declarations.values() for p in doc.get("exclude", {})}),
        # Docker's containerd image store can assign RepoDigests to local
        # builds too. Compose's build declarations identify those reliably.
        "local_image_names": sorted(built_images),
        # Include sidecars sharing a built image, even when a running container
        # still references an older tag than today's Compose configuration.
        "local_image_services": sorted(name for name, service in services.items()
                                       if service["image"] in built_images),
        "postgres": {name: doc["postgres"] for name, doc in declarations.items() if doc.get("postgres")},
        "quiesce": {name: doc["quiesce"] for name, doc in declarations.items() if doc.get("quiesce")},
        "project": json.loads(subprocess.check_output(["docker", "compose", "config", "--no-env-resolution", "--format", "json"], cwd=host_dir, text=True))["name"],
        "save_images": merged(declarations, "save_images"),
        "adapters": adapters,
        "volume_paths": volume_paths,
    }
    result["sources"] = sorted(set(result["sources"] + [p for n, p in volume_paths.items() if n != adapters.get("nextcloud-aio", {}).get("database_volume")]))
    result["sqlite_scan"] += list(volume_paths.values()) if not adapters else [p for n,p in volume_paths.items() if not n.startswith("nextcloud_aio_")]
    result["sqlite_scan"] += [p for name,q in result["quiesce"].items() if name not in adapters and q.get("method") == "stop" for p in q.get("copy", [])]
    result["local_image_services"] += result["save_images"]
    (stage / "plan.json").write_text(json.dumps(result, indent=2) + "\n")
    (stage / "excludes.txt").write_text("\n".join(excludes) + "\n")
    print(f"Backup plan: {len(result['sources'])} paths, {len(result['sqlite'])} SQLite files, "
          f"{sum(map(len, result['postgres'].values()))} PostgreSQL databases")


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, **kwargs)


def pg_dump(stage, database):
    destination = stage / "databases" / "postgres" / f"{database}.dump"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with destination.open("wb") as output:
        docker("exec", POSTGRES_CONTAINER, "sh", "-ec",
               f'exec pg_dump -U "$POSTGRES_USER" --format=custom --dbname="{database}"',
               stdout=output, timeout=900)
    print(f"Dumped PostgreSQL database {database} ({destination.stat().st_size} bytes)", flush=True)


def check_space(stage, planned):
    roots = copy_roots(p for q in planned["quiesce"].values() for p in q.get("copy", []))
    size = sum(int(subprocess.check_output(["du", "-sb", "--", p], text=True).split()[0]) for p in roots)
    if size + max(2 * 1024**3, size // 10) > shutil.disk_usage(stage).free:
        raise RuntimeError("Insufficient free space for coordinated copies and recovery headroom")


def dump_postgres(stage):
    stage = Path(stage)
    planned = json.loads((stage / "plan.json").read_text())
    check_space(stage, planned)
    globals_file = stage / "databases" / "postgres" / "globals.sql"
    globals_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if planned["postgres"]:
        with globals_file.open("wb") as output:
            docker("exec", POSTGRES_CONTAINER, "sh", "-ec",
                   'exec pg_dumpall -U "$POSTGRES_USER" --globals-only', stdout=output, timeout=300)
    for stack in sorted(set(planned["postgres"]) | set(planned["quiesce"])):
        databases = planned["postgres"].get(stack, [])
        quiesce = planned["quiesce"].get(stack)
        if not quiesce:
            for database in databases:
                pg_dump(stage, database)
            continue
        running = set(subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).split())
        services = [s for s in quiesce["services"] if s in running]
        paused = []
        method = quiesce.get("method", "pause")
        adapter = None
        cleanup = ExitStack()
        try:
            if stack in planned.get("adapters", {}):
                import nextcloud
                nextcloud.prepare(cleanup)
                adapter = nextcloud
            for service in services:
                paused.append(service)
                docker(method, service)
            print(f"Paused {', '.join(paused) or 'nothing'} for a consistent {stack} backup", flush=True)
            for database in databases:
                pg_dump(stage, database)
            if adapter:
                adapter.capture(stage)
            for source in quiesce.get("copy", []):
                target = stage / "quiesced" / source.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                subprocess.run(["cp", "-a", "--reflink=auto", source, str(target)], check=True, timeout=900)
        finally:
            errors = []
            for service in reversed(paused):
                result = subprocess.run(["docker", "unpause" if method == "pause" else "start", service], check=False)
                if result.returncode:
                    errors.append(service)
            cleanup.close()
            if errors:
                raise RuntimeError("Failed to resume: " + ", ".join(errors))
            if paused:
                print(f"Resumed {', '.join(reversed(paused))}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--infra", default=os.environ.get("INFRA_DIR", DEFAULT_INFRA))
    parser.add_argument("--host", default=os.environ.get("INFRA_HOST", "server-1"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").add_argument("--live", action="store_true")
    sub.add_parser("plan").add_argument("stage")
    sub.add_parser("dump-postgres").add_argument("stage")
    args = parser.parse_args()
    if args.command == "check":
        check(args.infra, args.host, args.live)
        print("Backup declarations cover every Compose mount" + (" and database" if args.live else ""))
    elif args.command == "plan":
        plan(args.infra, args.host, args.stage)
    else:
        dump_postgres(args.stage)


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise InterruptedError("Capture interrupted; writers will be resumed")
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    main()
