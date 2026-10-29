#!/usr/bin/env python3
"""Check ingress protections in Caddy's adapted JSON, using example hostnames."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
from string import Template
import sys


MEDIA_UPSTREAMS = {
    "MEDIA_JELLYFIN_DOMAIN": "127.0.0.1:8096",
    "MEDIA_QBITTORRENT_DOMAIN": "127.0.0.1:8080",
    "MEDIA_GLUETUN_DOMAIN": "127.0.0.1:18000",
    "MEDIA_PROWLARR_DOMAIN": "127.0.0.1:9696",
    "MEDIA_SONARR_DOMAIN": "127.0.0.1:8989",
    "MEDIA_RADARR_DOMAIN": "127.0.0.1:7878",
    "MEDIA_FLARESOLVERR_DOMAIN": "127.0.0.1:18191",
    "MEDIA_MAKEMKV_DOMAIN": "127.0.0.1:5800",
    "MEDIA_HANDBRAKE_DOMAIN": "127.0.0.1:5801",
}


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


def check_push_door(sites, host, name, allowed):
    """A write-only ingress: Basic auth before every proxy, fixed paths, 403 otherwise."""
    require(host in sites, f"Missing {name} ingress route")
    boundaries = list(loki_proxies([sites[host]]))
    require(boundaries, f"Missing {name} proxy")
    for paths, auth in boundaries:
        require(paths and paths <= allowed, f"{name} proxy permits paths outside {sorted(allowed)}")
        require(auth, f"{name} proxy has no preceding Basic authentication")
    require(set().union(*(paths for paths, _ in boundaries)) == allowed,
            f"{name} ingress must preserve all of {sorted(allowed)}")
    require(any(node.get("handler") == "static_response" and
                str(node.get("status_code")) == "403" for node in nodes(sites[host])),
            f"Missing {name} denial response")


def check_cpa_catalog(site, cpa_upstream, policy_upstream):
    """Assert denial order and the exact model-list-only enrichment branch."""
    outer = site.get("handle", [])
    require(len(outer) == 1 and outer[0].get("handler") == "subroute",
            "Unexpected CPA site structure")
    wrapper = outer[0].get("routes", [])
    require(len(wrapper) == 1 and not wrapper[0].get("match"),
            "CPA routing must have one unconditional ordered route")
    inner = wrapper[0].get("handle", [])
    require(len(inner) == 1 and inner[0].get("handler") == "subroute",
            "CPA ordered route is missing")
    routes = inner[0].get("routes", [])
    require(len(routes) == 3, "CPA must have denial, model list, then direct fallback")
    deny, models, fallback = routes
    require(deny.get("match") == [{"path": [
        "/v0/management", "/v0/management/*", "/v8/management", "/v8/management/*"
    ]}], "CPA v0 and v8 management denial must precede all proxies")
    require(deny.get("handle") == [{"handler": "static_response", "status_code": 403}],
            "CPA management paths must return 403")
    require(models.get("match") == [{"method": ["GET", "HEAD"], "path": ["/v1/models"]}],
            "Only GET/HEAD CPA model lists may go through Policy Proxy")
    require(not fallback.get("match") and models.get("group") and
            models.get("group") == fallback.get("group"),
            "CPA model-list and direct fallback handlers must be mutually exclusive")
    rewrites = [node for node in nodes(models) if node.get("handler") == "rewrite"]
    require(rewrites == [{"handler": "rewrite", "uri": "/v1/upstream-models"}],
            "CPA model-list rewrite must preserve query strings and use the raw catalog view")
    for route, upstream in [(models, policy_upstream), (fallback, cpa_upstream)]:
        proxies = [node for node in nodes(route) if node.get("handler") == "reverse_proxy"]
        require(len(proxies) == 1 and proxies[0].get("upstreams") == [{"dial": upstream}],
                "Incorrect CPA model-list or inference upstream")
        require(proxies[0].get("headers", {}).get("request") == {"delete": ["Tailscale-*"]},
                "CPA proxy must retain credentials while stripping Tailscale headers")
    require(not any(node.get("handler") == "rewrite" for node in nodes(fallback)),
            "CPA inference fallback must not rewrite requests")


def check_career_ops_navigation(site):
    """Only explicit page clicks may fall back from Fetch Metadata to Origin."""
    expected_match = {
        "method": ["GET", "HEAD"],
        "header": {
            "Sec-Fetch-Dest": ["document"],
            "Sec-Fetch-Mode": ["navigate"],
            "Sec-Fetch-User": ["?1"],
        },
        "header_regexp": {"Sec-Fetch-Site": {
            "name": "navigation_site", "pattern": "^(same-site|cross-site)$",
        }},
        "not": [{"path": ["/api", "/api/*"]}],
    }
    modifiers = [node for node in nodes(site)
                 if node.get("handler") == "headers" and node.get("request")]
    require(len(modifiers) == 1 and modifiers[0]["request"] == {"delete": ["Sec-Fetch-Site"]},
            "Career Ops must preserve Origin and limit Fetch Metadata modification")
    routes = [node for node in nodes(site) if modifiers[0] in node.get("handle", [])]
    require(len(routes) == 1 and routes[0].get("match") == [expected_match],
            "Career Ops navigation exception must exclude API, fetch, iframe and POST requests")


def check(config, loki_host, cpa_host, host_domain, otel_host, cpa_upstream=None, policy_upstream=None):
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

    check_push_door(sites, loki_host, "Loki", {"/loki/api/v1/push", "/ready"})
    check_push_door(sites, otel_host, "OTLP", {"/v1/traces", "/v1/logs", "/v1/metrics"})

    if cpa_host is not None:
        require(cpa_host in sites, "Missing direct CPA route")
        check_cpa_catalog(sites[cpa_host], cpa_upstream, policy_upstream)
    print("Host identity, Caddy proxy listeners, and Loki/OTLP path/auth boundaries verified.")
    if cpa_host is not None:
        print("CPA management denial, model-list scope, and direct inference route verified.")
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
        check(config, examples["LOKI_DOMAIN"], cpa_host, examples["CADDY_HOST_DOMAIN"],
              examples["OTEL_DOMAIN"], Template(examples["CPA_UPSTREAM"]).substitute(examples),
              Template(examples["POLICY_PROXY_UPSTREAM"]).substitute(examples))
        for variable, upstream in MEDIA_UPSTREAMS.items():
            if variable not in examples:
                continue  # Other hosts need not run media.
            routes = [node for node in nodes(config)
                      if any(examples[variable] in match.get("host", [])
                             for match in node.get("match", []))]
            if args.absent_domain in MEDIA_UPSTREAMS:
                require(not routes, "Incomplete media routing must omit all media sites")
            else:
                require(len(routes) == 1, f"Missing or duplicate media route: {variable}")
                proxies = [node for node in nodes(routes[0]) if node.get("handler") == "reverse_proxy"]
                require(len(proxies) == 1 and proxies[0].get("upstreams") == [{"dial": upstream}],
                        f"Incorrect media upstream: {variable}")
        if "CAREER_OPS_DOMAIN" in examples:
            domain = examples["CAREER_OPS_DOMAIN"]
            routes = [node for node in nodes(config)
                      if any(domain in match.get("host", []) for match in node.get("match", []))]
            if args.absent_domain == "CAREER_OPS_DOMAIN":
                require(not routes, "Missing Career Ops routing must omit its site")
            else:
                require(len(routes) == 1, "Missing or duplicate Career Ops route")
                proxies = [node for node in nodes(routes[0]) if node.get("handler") == "reverse_proxy"]
                require(len(proxies) == 1 and proxies[0].get("upstreams") == [{"dial": "127.0.0.1:8890"}],
                        "Incorrect Career Ops upstream")
                require(proxies[0].get("headers", {}).get("request", {}).get("delete") == ["Tailscale-*"],
                        "Career Ops must strip inbound Tailscale identity headers")
                check_career_ops_navigation(routes[0])
            print("Career Ops HTTPS upstream and identity-header boundary verified.")
        print("Media HTTPS upstreams and optional routing verified.")
        if args.absent_domain:
            domain = examples[args.absent_domain]
            require(not any(domain in node.get("host", []) for node in nodes(config)),
                    f"Site with missing {args.absent_domain} was not omitted")
            print(f"Site with missing {args.absent_domain} is omitted; remaining ingress is valid.")
    except (KeyError, ValueError) as error:
        sys.exit(f"Routing validation failed: {error}")
