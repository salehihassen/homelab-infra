#!/usr/bin/env python3
"""Validate the public host's deliberate service, storage and ingress contracts."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent / "hosts/server-2"
APPS = {"flowise", "n8n", "nextcloud-aio-mastercontainer", "open-webui", "mock-traveler", "personal-network", "caddy", "alloy"}
UPSTREAMS = {"FLOWISE_DOMAIN": "127.0.0.1:3003", "N8N_DOMAIN": "127.0.0.1:5678", "NEXTCLOUD_DOMAIN": "127.0.0.1:11000", "NEXTCLOUD_AIO_DOMAIN": "127.0.0.1:8080", "OPENWEBUI_DOMAIN": "127.0.0.1:3009", "MOCK_TRAVELER_DOMAIN": "127.0.0.1:8090", "PERSONAL_NETWORK_DOMAIN": "127.0.0.1:3100"}


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def compose(doc):
    services = doc["services"]
    assert set(services) == APPS, "Only active applications and the log shipper belong in server-2"
    for name, service in services.items():
        assert service.get("healthcheck") or name == "alloy", name + ": missing health check"
        assert service.get("restart") == "unless-stopped", name + ": restart policy"
        assert service.get("mem_limit"), name + ": memory bound"
        assert service.get("logging", {}).get("options", {}).get("max-size") == "10m", name + ": bounded logs"
        assert not service.get("privileged"), name + ": privileged is forbidden"
        assert "no-new-privileges:true" in service.get("security_opt", []), name + ": prevent privilege gain"
    for name, volume in [("n8n", "n8n_data"), ("caddy", "caddy_data"), ("nextcloud-aio-mastercontainer", "nextcloud_aio_mastercontainer")]:
        definition = doc["volumes"][volume]
        assert definition.get("external") and definition.get("name") == volume, name + ": preserve the existing external volume"
    controller = services["nextcloud-aio-mastercontainer"]
    assert controller["container_name"] == "nextcloud-aio-mastercontainer"
    assert not any(n.startswith("nextcloud-aio-") and n != "nextcloud-aio-mastercontainer" for n in services), "AIO owns its children"
    assert any(m["target"] == "/var/run/docker.sock" for m in controller["volumes"])
    assert services["caddy"].get("network_mode") == "host"
    for name, service in services.items():
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                assert mount.get("bind", {}).get("create_host_path") is False, name + ": bind mounts must fail closed"
        if name != "nextcloud-aio-mastercontainer" and name != "alloy":
            assert not any(m["target"] == "/var/run/docker.sock" for m in service.get("volumes", [])), name + ": unexpected Docker access"
    assert services["alloy"]["ports"][0]["host_ip"] == "127.0.0.1", "Alloy admin is loopback-only"


def routing(doc):
    examples = {}
    for p in ROOT.glob("*/routing.env.example"):
        examples.update(l.split("=", 1) for l in p.read_text().splitlines() if l and not l.startswith("#"))
    routes = [n for n in nodes(doc) if any("host" in m for m in n.get("match", []))]
    actual = {h for route in routes for m in route["match"] for h in m.get("host", [])}
    # Kaneo runs on K3s; its private upstream is the Service ClusterIP:5173.
    upstreams = UPSTREAMS | {"KANEO_PUBLIC_DOMAIN": examples["KANEO_K3S_UPSTREAM"]}
    expected = {examples[k] for k in upstreams} | {examples[k] for k in ["PROFILE_DOMAIN", "PROFILE_ALIAS_DOMAIN", "RESOURCES_DOMAIN"]}
    assert actual == expected, "Only explicitly configured active app and redirect hosts may be served"
    for key, upstream in upstreams.items():
        route = next(r for r in routes if any(examples[key] in m.get("host", []) for m in r["match"]))
        proxies = [n for n in nodes(route) if n.get("handler") == "reverse_proxy"]
        assert len(proxies) == 1 and proxies[0].get("upstreams") == [{"dial": upstream}], key + ": exact backend"
        proxy = proxies[0]
        assert proxy.get("headers", {}).get("request", {}).get("delete") == ["Tailscale-*"], key + ": strip identity headers"
        skip = proxy.get("transport", {}).get("tls", {}).get("insecure_skip_verify", False)
        assert skip == (key == "NEXTCLOUD_AIO_DOMAIN"), key + ": TLS verification exception is controller-only"
    # Mission Control: tailnet sources are proxied; every other source gets 403 first.
    servers = {tuple(s["listen"]): s for s in doc["apps"]["http"]["servers"].values()}
    assert set(servers) == {(":443",), (":9081",)}, "Only HTTPS sites and the tailnet dashboard listen"
    deny, proxy = servers[(":9081",)]["routes"]
    tailnet = [{"not": [{"remote_ip": {"ranges": ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]}}]}]
    assert deny["match"] == tailnet and deny["handle"] == [{"handler": "static_response", "status_code": 403}], "FLUX_UI: tailnet sources only"
    assert "match" not in proxy and proxy["handle"][0].get("upstreams") == [{"dial": examples["FLUX_UI_K3S_UPSTREAM"]}], "FLUX_UI: exact backend"
    assert proxy["handle"][0].get("headers", {}).get("request", {}).get("delete") == ["Tailscale-*"], "FLUX_UI: strip identity headers"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["compose", "routing"])
    args = parser.parse_args()
    try:
        globals()[args.mode](json.load(sys.stdin))
    except (AssertionError, KeyError, ValueError) as error:
        sys.exit("server-2 contract: " + str(error))
    print("server-2 " + args.mode + " contracts verified")
