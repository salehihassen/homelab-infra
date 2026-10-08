"""Regression checks for missing routing settings and conflicting IP allocations."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_networks", REPO / "scripts/check-networks.py")
networks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(networks)


class CaddyStartupTests(unittest.TestCase):
    def test_compose_entrypoint_launches_caddy_after_rendering(self):
        host = REPO / "hosts/server-1"
        environment = {**os.environ, "STACK_ENV_FILE": ".env.example",
                       "STACK_ROUTING_ENV_FILE": "routing.env.example"}
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(host / ".env.example"),
             "--project-directory", str(host), "--file", str(host / "compose.yaml"),
             "config", "--no-env-resolution", "--format", "json"],
            env=environment, text=True, capture_output=True, check=True,
        )
        service = json.loads(result.stdout)["services"]["caddy"]
        # Compose discards the image CMD when a custom entrypoint is supplied.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "caddy"
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STARTUP_ARGS"\n')
            executable.chmod(0o755)
            arguments = root / "arguments"
            entrypoint = [str(REPO / "hosts/server-1/caddy/render-sites.sh")
                          if part == "/etc/caddy/render-sites.sh" else part
                          for part in service["entrypoint"]]
            # Run the container's dedicated BusyBox shell with the host shell;
            # the separate container smoke check exercises its capabilities.
            entrypoint[0] = "/bin/sh"
            subprocess.run(
                entrypoint + (service.get("command") or []),
                env={"PATH": f"{root}:{os.environ['PATH']}",
                     "STARTUP_ARGS": str(arguments), "CADDY_SITE_ROOT": str(root),
                     "CADDY_SITE_IMPORTS": str(root / "imports.caddy")},
                text=True, capture_output=True, check=True,
            )
            self.assertTrue(arguments.exists(), "The entrypoint never launched Caddy")
            self.assertEqual(arguments.read_text().splitlines(),
                             ["run", "--config", "/etc/caddy/Caddyfile",
                              "--adapter", "caddyfile"])


class RoutingSelectionTests(unittest.TestCase):
    def test_missing_routing_values_skip_only_the_affected_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in {
                "available": "https://{$AVAILABLE_DOMAIN} {\n bind {$CADDY_BIND_ADDRESSES}\n}\n",
                "missing": "https://{$MISSING_DOMAIN} {}\n",
                "incomplete": "https://{$AVAILABLE_DOMAIN} {\n respond {$MISSING_AUTH}\n}\n",
            }.items():
                (root / name).mkdir()
                (root / name / "site.caddy").write_text(content)
            imports = root / "imports.caddy"
            environment = {"PATH": os.environ["PATH"], "CADDY_SITE_ROOT": str(root),
                           "CADDY_SITE_IMPORTS": str(imports),
                           "AVAILABLE_DOMAIN": "available.example",
                           "CADDY_BIND_ADDRESSES": "100.64.0.10"}
            result = subprocess.run(
                ["sh", str(REPO / "hosts/server-1/caddy/render-sites.sh"), "/bin/true"],
                env=environment, text=True, capture_output=True, check=True,
            )
            self.assertEqual(imports.read_text(), f'import "{root}/available/site.caddy"\n')
            self.assertIn("skipping missing", result.stderr)
            self.assertIn("skipping incomplete", result.stderr)

    def test_no_available_sites_still_creates_a_valid_empty_import_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            imports = root / "imports.caddy"
            subprocess.run(
                ["sh", str(REPO / "hosts/server-1/caddy/render-sites.sh"), "/bin/true"],
                env={"PATH": os.environ["PATH"], "CADDY_SITE_ROOT": str(root),
                     "CADDY_SITE_IMPORTS": str(imports)}, check=True,
            )
            self.assertEqual(imports.read_text(), "")


class NetworkAllocationTests(unittest.TestCase):
    def test_rejects_a_broad_subnet_covering_a_reserved_network(self):
        config = {"networks": {
            "app": {"ipam": {"config": [{"subnet": "172.23.0.0/16"}]}},
            "ci": {"ipam": {"config": [{"subnet": "172.23.0.0/24"}]}},
        }}
        self.assertTrue(any("overlaps" in error for error in networks.check(config)))

    def test_rejects_static_ip_outside_its_network(self):
        config = {"networks": {"app": {"ipam": {"config": [{"subnet": "10.66.0.0/24"}]}}},
                  "services": {"app": {"networks": {"app": {"ipv4_address": "10.66.1.2"}}}}}
        self.assertTrue(any("outside" in error for error in networks.check(config)))


if __name__ == "__main__":
    unittest.main()
