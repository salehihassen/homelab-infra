#!/usr/bin/env python3
"""Validate subnets and prevent static/dynamic IP conflicts in Compose JSON."""

import ipaddress
import json
import sys


def check(config):
    errors = []
    pools = {}
    dynamic_pools = {}
    static_owners = {}
    dynamic_clients = set()
    for name, network in config.get("networks", {}).items():
        pools[name] = []
        dynamic_pools[name] = []
        for allocation in network.get("ipam", {}).get("config", []):
            try:
                subnet = ipaddress.ip_network(allocation["subnet"])
            except (KeyError, ValueError) as error:
                errors.append(f"{name}: invalid subnet: {error}")
                continue
            for other_name, other_pools in pools.items():
                for other in other_pools:
                    if subnet.version == other.version and subnet.overlaps(other):
                        errors.append(f"{name}: {subnet} overlaps {other_name}: {other}")
            pools[name].append(subnet)
            if "ip_range" in allocation:
                try:
                    dynamic = ipaddress.ip_network(allocation["ip_range"])
                except ValueError as error:
                    errors.append(f"{name}: invalid ip_range: {error}")
                    continue
                if dynamic.version != subnet.version or not dynamic.subnet_of(subnet):
                    errors.append(f"{name}: ip_range {dynamic} is outside subnet {subnet}")
                else:
                    dynamic_pools[name].append(dynamic)

    for service, definition in config.get("services", {}).items():
        for name, options in (definition.get("networks") or {}).items():
            for version, key in ((4, "ipv4_address"), (6, "ipv6_address")):
                if not options or key not in options:
                    dynamic_clients.add((name, version))
            for key in ("ipv4_address", "ipv6_address"):
                if not options or key not in options:
                    continue
                try:
                    address = ipaddress.ip_address(options[key])
                except ValueError as error:
                    errors.append(f"{service}/{name}: invalid address: {error}")
                    continue
                if not any(address.version == subnet.version and address in subnet
                           for subnet in pools.get(name, [])):
                    errors.append(f"{service}/{name}: {address} is outside its assigned subnet")
                owner_key = (name, address)
                if owner_key in static_owners:
                    errors.append(f"{service}/{name}: duplicate static address {address} "
                                  f"used by {static_owners[owner_key]}")
                static_owners[owner_key] = service
                if any(address.version == pool.version and address in pool
                       for pool in dynamic_pools.get(name, [])):
                    errors.append(f"{service}/{name}: static address {address} is inside dynamic ip_range")

    for name, address in static_owners:
        if (name, address.version) not in dynamic_clients:
            continue
        # Every subnet of a mixed-address family needs its own restricted pool;
        # an unrestricted second subnet would still allow dynamic collisions.
        for subnet in pools.get(name, []):
            if subnet.version == address.version and not any(
                pool.subnet_of(subnet) for pool in dynamic_pools.get(name, [])
                if pool.version == subnet.version
            ):
                error = f"{name}: mixed static/dynamic addresses require an ip_range in {subnet}"
                if error not in errors:
                    errors.append(error)
    return errors


if __name__ == "__main__":
    errors = check(json.load(sys.stdin))
    if errors:
        sys.exit("Network validation failed:\n" + "\n".join(errors))
    print("Network subnets, static addresses, and dynamic allocation ranges are valid.")
