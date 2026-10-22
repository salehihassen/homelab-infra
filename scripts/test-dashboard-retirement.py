#!/usr/bin/env python3
"""Exercise dashboard retirement with the pinned Grafana and disposable state.

Only a loopback test listener, test credentials and temporary provisioning files
are used. Production Grafana, its credentials and its database are never read.
"""

import argparse
import base64
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]


def run(image):
    name = "grafana-retirement-test-" + secrets.token_hex(4)
    headers = {"Authorization": "Basic " + base64.b64encode(b"admin:replace-with-password").decode()}
    with tempfile.TemporaryDirectory(prefix="grafana-retirement-test-") as directory:
        root = Path(directory)
        dashboards = root / "dashboards"
        dashboards.mkdir()
        source = REPO / "hosts/server-1/observability/grafana/provisioning/dashboards"
        for filename in ["agents.yaml", "agents.json", "telemetry-health.json"]:
            shutil.copyfile(source / filename, dashboards / filename)
        retired = dashboards / "condenseit.json"
        retired.write_text(json.dumps({"uid": "condenseit", "title": "Condenseit",
                                      "schemaVersion": 39, "panels": []}))
        try:
            subprocess.run([
                "docker", "run", "--detach", "--rm", "--name", name,
                "--read-only", "--user", "1000:1000", "--memory", "512m",
                "--publish", "127.0.0.1::3000",
                "--tmpfs", "/var/lib/grafana:uid=1000,gid=1000",
                "--tmpfs", "/var/log/grafana:uid=1000,gid=1000",
                "--tmpfs", "/tmp",
                "--mount", f"type=bind,src={root},dst=/etc/grafana/provisioning,readonly",
                "--env", "GF_SECURITY_ADMIN_USER=admin",
                "--env", "GF_SECURITY_ADMIN_PASSWORD=replace-with-password",
                "--env", "GF_ANALYTICS_REPORTING_ENABLED=false",
                "--env", "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
                "--env", "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false",
                "--env", "GF_PLUGINS_PREINSTALL_DISABLED=true",
                image,
            ], check=True, capture_output=True, text=True)
            container = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
            port = container["NetworkSettings"]["Ports"]["3000/tcp"][0]["HostPort"]
            endpoint = f"http://127.0.0.1:{port}"
            # Ignore inherited proxies even on hosts where NO_PROXY is unset.
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def api(path, method="GET", expected=200):
                request = urllib.request.Request(endpoint + path, headers=headers, method=method)
                try:
                    response = client.open(request, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    assert response.status == expected, (path, response.status, expected)
                    return json.load(response)

            deadline = time.monotonic() + 45
            while True:
                try:
                    api("/api/health")
                    break
                except (OSError, AssertionError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Test Grafana did not become ready")
                    time.sleep(0.2)

            original = api("/api/dashboards/uid/condenseit")
            assert original["meta"]["provisioned"] is True
            keep = {uid: api("/api/dashboards/uid/" + uid)["dashboard"]
                    for uid in ["agents", "telemetry-health"]}
            retired.unlink()
            api("/api/admin/provisioning/dashboards/reload", "POST")
            remaining = api("/api/dashboards/uid/condenseit")
            assert remaining["dashboard"] == original["dashboard"]
            assert remaining["meta"]["provisioned"] is False
            api("/api/dashboards/uid/condenseit", "DELETE")
            api("/api/dashboards/uid/condenseit", expected=404)
            api("/api/admin/provisioning/dashboards/reload", "POST")
            api("/api/dashboards/uid/condenseit", expected=404)
            for uid, dashboard in keep.items():
                actual = api("/api/dashboards/uid/" + uid)
                assert actual["meta"]["provisioned"] is True, uid
                assert actual["dashboard"] == dashboard, uid
            print("PASS: disableDeletion retains/unprovisions the retired dashboard; "
                  "only its UID is deleted; Agents and Telemetry health remain provisioned and unchanged.")
        except Exception:
            subprocess.run(["docker", "logs", "--tail", "30", name], check=False)
            raise
        finally:
            subprocess.run(["docker", "stop", "--time", "5", name], capture_output=True, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Grafana image; defaults to the tracked Compose pin")
    args = parser.parse_args()
    if args.image is None:
        import yaml
        stack = yaml.safe_load((REPO / "hosts/server-1/observability/compose.yaml").read_text())
        args.image = stack["services"]["grafana"]["image"]
    run(args.image)
