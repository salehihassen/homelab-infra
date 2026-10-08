#!/usr/bin/env python3
"""Validate subnet allocations and static IPs in resolved Compose JSON."""

import ipaddress
import json
import sys


def check(config):
    errors = []
    pools = {}
    for name, network in config.get("networks", {}).items():
        pools[name] = []
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

    for service, definition in config.get("services", {}).items():
        for name, options in (definition.get("networks") or {}).items():
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
    return errors


if __name__ == "__main__":
    errors = check(json.load(sys.stdin))
    if errors:
        sys.exit("Network validation failed:\n" + "\n".join(errors))
    print("Network subnets are disjoint and static IPs are within their assigned subnet.")
