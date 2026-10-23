"""Protect the exact scope and ordering of model-list enrichment."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("catalog_routing", Path(__file__).parents[1] / "check-routing.py")
routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing)


def site():
    def proxy(upstream):
        return {"handler": "reverse_proxy", "upstreams": [{"dial": upstream}],
                "headers": {"request": {"delete": ["Tailscale-*"]}}}
    deny = {"match": [{"path": ["/v0/management", "/v0/management/*", "/v8/management", "/v8/management/*"]}],
            "handle": [{"handler": "static_response", "status_code": 403}]}
    models = {"group": "exclusive", "match": [{"path": ["/v1/models"], "method": ["GET", "HEAD"]}],
              "handle": [{"handler": "subroute", "routes": [
                  {"handle": [{"handler": "rewrite", "uri": "/v1/upstream-models"}]},
                  {"handle": [proxy("policy:18443")]},
              ]}]}
    fallback = {"group": "exclusive", "handle": [{"handler": "subroute", "routes": [{"handle": [proxy("cpa:8317")]}]}]}
    return {"handle": [{"handler": "subroute", "routes": [{"handle": [{"handler": "subroute", "routes": [deny, models, fallback]}]}]}]}


class ModelCatalogRoutingTests(unittest.TestCase):
    def test_exact_ordered_configuration_is_accepted(self):
        routing.check_cpa_catalog(site(), "cpa:8317", "policy:18443")

    def test_broader_paths_methods_or_wrong_upstream_are_rejected(self):
        for mutation in ["path", "method", "upstream", "denial", "query", "credentials", "fallback"]:
            value = copy.deepcopy(site())
            routes = value["handle"][0]["routes"][0]["handle"][0]["routes"]
            model_routes = routes[1]["handle"][0]["routes"]
            if mutation == "path":
                routes[1]["match"][0]["path"] = ["/v1/*"]
            elif mutation == "method":
                routes[1]["match"][0]["method"].append("POST")
            elif mutation == "upstream":
                model_routes[1]["handle"][0]["upstreams"] = [{"dial": "cpa:8317"}]
            elif mutation == "denial":
                routes[0], routes[1] = routes[1], routes[0]
            elif mutation == "query":
                model_routes[0]["handle"][0]["uri"] += "?client_version=changed"
            elif mutation == "credentials":
                model_routes[1]["handle"][0]["headers"]["request"]["delete"].append("Authorization")
            else:
                routes[2]["group"] = "different"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                routing.check_cpa_catalog(value, "cpa:8317", "policy:18443")
