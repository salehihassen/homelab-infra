#!/usr/bin/env python3
"""Check that every host's stacks declare their cross-stack dependencies.

Each hosts/<host>/<stack>/compose.yaml lists the stacks it relies on under
`x-stack.requires`. A stack may only reference networks, services (depends_on,
network_mode: service:...), and secrets that it defines itself or that a
required stack defines. Every required stack must actually be used, and every
stack needs a README.md and a backup.yaml beside its compose file and an
entry in inventory.yaml.
Each network is defined by its owning stack with a required subnet variable.

Usage: python3 scripts/check-stacks.py [hosts/<host> ...]
"""

from pathlib import Path
import re
import sys

import yaml

REPO = Path(__file__).resolve().parent.parent


def load_stack(path):
    doc = yaml.safe_load(path.read_text()) or {}
    services = doc.get("services") or {}
    defined = {
        "service": set(services),
        "network": set(doc.get("networks") or {}),
        "secret": set(doc.get("secrets") or {}),
    }
    used = {"service": set(), "network": set(), "secret": set()}
    for service in services.values():
        networks = service.get("networks") or []
        used["network"].update(networks if isinstance(networks, list) else networks.keys())
        depends = service.get("depends_on") or []
        used["service"].update(depends if isinstance(depends, list) else depends.keys())
        mode = service.get("network_mode", "")
        if mode.startswith("service:"):
            used["service"].add(mode.split(":", 1)[1])
        for secret in service.get("secrets") or []:
            used["secret"].add(secret if isinstance(secret, str) else secret["source"])
    requires = (doc.get("x-stack") or {}).get("requires")
    return defined, used, requires


def check_host(host_dir):
    errors = []
    top = yaml.safe_load((host_dir / "compose.yaml").read_text())
    stacks = {}
    for entry in top.get("include", []):
        rel = entry if isinstance(entry, str) else entry["path"]
        path = host_dir / rel
        stacks[Path(rel).parent.name] = path
    on_disk = {p.parent.name for p in host_dir.glob("*/compose.yaml")}
    for missing in sorted(on_disk - set(stacks)):
        errors.append(f"{missing}: has compose.yaml but is not included by {host_dir.name}/compose.yaml")

    loaded = {}
    for name, path in stacks.items():
        if not path.exists():
            errors.append(f"{name}: included file {path} is missing")
            continue
        loaded[name] = load_stack(path)
        doc = yaml.safe_load(path.read_text()) or {}
        for network, config in (doc.get("networks") or {}).items():
            pools = ((config or {}).get("ipam") or {}).get("config") or []
            subnet = pools[0].get("subnet", "") if len(pools) == 1 else ""
            if not isinstance(subnet, str) or not re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*:\?.+\}", subnet):
                errors.append(f"{name}: network `{network}` needs exactly one required subnet variable")
        for extra in ("README.md", "backup.yaml"):
            if not (path.parent / extra).exists():
                errors.append(f"{name}: missing {extra}")

    owner = {}
    for name, (defined, _, _) in loaded.items():
        for kind, names in defined.items():
            for item in names:
                if (kind, item) in owner:
                    errors.append(f"{name}: {kind} `{item}` is also defined by {owner[(kind, item)]}")
                owner[(kind, item)] = name

    for name, (defined, used, requires) in loaded.items():
        if requires is None:
            errors.append(f"{name}: missing x-stack.requires (use [] when there are none)")
            continue
        for req in requires:
            if req not in loaded:
                errors.append(f"{name}: requires unknown stack `{req}`")
        needed = set()
        for kind, names in used.items():
            for item in sorted(names - defined[kind]):
                provider = owner.get((kind, item))
                if provider is None:
                    errors.append(f"{name}: {kind} `{item}` is not defined by any stack")
                    continue
                needed.add(provider)
                if provider not in requires:
                    errors.append(f"{name}: uses {kind} `{item}` from `{provider}` without requiring it")
        for req in sorted(set(requires) - needed - {name}):
            if req in loaded:
                errors.append(f"{name}: requires `{req}` but uses nothing from it")
    return errors


def check_inventory(host_dir):
    path = REPO / "inventory.yaml"
    if not path.exists():  # the public showcase has no inventory
        return []
    inventory = yaml.safe_load(path.read_text())
    listed = set((inventory.get("stacks") or {}).get(host_dir.name) or {})
    included = {p.parent.name for p in host_dir.glob("*/compose.yaml")}
    return ([f"{s}: missing from inventory.yaml" for s in sorted(included - listed)] +
            [f"{s}: in inventory.yaml but has no stack directory" for s in sorted(listed - included)])


def main(argv):
    # Symlinked host directories are aliases of a real one; check each host once.
    hosts = [REPO / a for a in argv] if argv else sorted(
        p.parent for p in REPO.glob("hosts/*/compose.yaml") if not p.parent.is_symlink())
    errors = [f"{h.name}/{e}" for h in hosts for e in check_host(h) + check_inventory(h)]
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"Stack dependencies OK for: {', '.join(h.name for h in hosts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
