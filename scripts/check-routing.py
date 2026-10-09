#!/usr/bin/env python3
"""Check ingress protections in Caddy's adapted JSON, using example hostnames."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import sys


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_listener(address):
    host = address.rsplit(":", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip in ipaddress.ip_network(
        "100.64.0.0/10" if ip.version == 4 else "fd7a:115c:a1e0::/48"
    )


def loki_proxies(routes, paths=None, authenticated=False):
    """Follow route branches without carrying authentication across siblings."""
    for route in routes:
        branch_paths = paths
        matches = route.get("match", [])
        if matches:
            if all("path" in match for match in matches):
                matched = {path for match in matches for path in match["path"]}
                branch_paths = matched if paths is None else paths & matched
            # A matcher without paths leaves the inherited path boundary intact.
        auth = authenticated
        for handler in route.get("handle", []):
            if handler.get("handler") == "authentication":
                auth = bool(handler.get("providers", {}).get("http_basic", {}).get("accounts"))
            elif handler.get("handler") == "subroute":
                yield from loki_proxies(handler.get("routes", []), branch_paths, auth)
            elif handler.get("handler") == "reverse_proxy":
                yield branch_paths, auth


def check(config, loki_host, cpa_host, host_domain):
    sites = {}
    wildcard_redirects = 0
    for server in config.get("apps", {}).get("http", {}).get("servers", {}).values():
        proxies = any(node.get("handler") == "reverse_proxy" for node in nodes(server))
        listeners = server.get("listen", [])
        if proxies:
            require(listeners and all(private_listener(addr) for addr in listeners),
                    "A reverse-proxy server has a non-tailnet listener")
        elif any(not private_listener(addr) for addr in listeners):
            handlers = [node for node in nodes(server) if "handler" in node]
            require(handlers and all(
                node["handler"] == "subroute" or (
                    node["handler"] == "static_response" and
                    str(node.get("status_code", "")).startswith("3") and
                    node.get("headers", {}).get("Location")
                ) for node in handlers),
                "Non-tailnet listener must contain only the existing redirect exception")
            wildcard_redirects += 1
        for route in server.get("routes", []):
            for match in route.get("match", []):
                for host in match.get("host", []):
                    require(host not in sites, "Duplicate hostname route")
                    sites[host] = route

    require(host_domain in sites, "Missing host identity route")
    require(not any(node.get("handler") == "reverse_proxy" for node in nodes(sites[host_domain])),
            "Host identity route must not proxy to an application")
    require(any(node.get("handler") == "static_response" and
                str(node.get("status_code")) == "200" for node in nodes(sites[host_domain])),
            "Host identity route must serve a Caddy response")

    require(loki_host in sites, "Missing Loki ingress route")
    allowed = {"/loki/api/v1/push", "/ready"}
    boundaries = list(loki_proxies([sites[loki_host]]))
    require(boundaries, "Missing Loki proxy")
    for paths, auth in boundaries:
        require(paths and paths <= allowed, "Loki proxy permits paths outside push/readiness")
        require(auth, "Loki proxy has no preceding Basic authentication")
    require(set().union(*(paths for paths, _ in boundaries)) == allowed,
            "Loki ingress must preserve both push and readiness")
    require(any(node.get("handler") == "static_response" and
                str(node.get("status_code")) == "403" for node in nodes(sites[loki_host])),
            "Missing Loki denial response")

    if cpa_host is not None:
        require(cpa_host in sites, "Missing direct CPA route")
        inner = sites[cpa_host].get("handle", [])
        require(len(inner) == 1 and inner[0].get("handler") == "subroute",
                "Unexpected CPA route structure; review management protection")
        routes = inner[0].get("routes", [])
        require(routes and {p for m in routes[0].get("match", []) for p in m.get("path", [])}
                == {"/v0/management", "/v0/management/*", "/v8/management", "/v8/management/*"},
                "CPA v0 and v8 management denial must precede proxy")
        require(routes[0].get("handle") == [{"handler": "static_response", "status_code": 403}],
                "CPA management paths must return 403")
    print("Host identity, Caddy proxy listeners, and Loki path/auth boundary verified.")
    if cpa_host is not None:
        print("CPA management denial verified.")
    if wildcard_redirects:
        print(f"Observed {wildcard_redirects} non-tailnet redirect server(s); review redirect exposure separately.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--absent-domain", help="Example env key whose site must be omitted")
    args = parser.parse_args()
    try:
        host_dir = Path(__file__).resolve().parent.parent / "hosts" / os.environ.get("INFRA_HOST", "server-1")
        paths = [host_dir / "caddy/.env.example", *host_dir.glob("*/routing.env.example")]
        examples = dict(line.split("=", 1) for path in paths
                        for line in path.read_text().splitlines()
                        if line and not line.startswith("#") and "=" in line)
        config = json.load(sys.stdin)
        cpa_host = None if args.absent_domain == "CPA_DOMAIN" else examples["CPA_DOMAIN"]
        check(config, examples["LOKI_DOMAIN"], cpa_host, examples["CADDY_HOST_DOMAIN"])
        if args.absent_domain:
            domain = examples[args.absent_domain]
            require(not any(domain in node.get("host", []) for node in nodes(config)),
                    f"Site with missing {args.absent_domain} was not omitted")
            print(f"Site with missing {args.absent_domain} is omitted; remaining ingress is valid.")
    except (KeyError, ValueError) as error:
        sys.exit(f"Routing validation failed: {error}")
