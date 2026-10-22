#!/usr/bin/env python3
"""Exercise the real Langfuse transform twice in an isolated Alloy container.

Uses the Compose-pinned Alloy image, synthetic data, loopback-only listeners,
and an in-memory OTLP capture server. No production backends or secrets.
"""

import argparse
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
HARNESS = "langfuse.trace.metadata.harness"
TAGS = "langfuse.trace.tags"


def attribute(key, value):
    if isinstance(value, list):
        encoded = {"arrayValue": {"values": [{"stringValue": x} for x in value]}}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}
    else:
        encoded = {"stringValue": value}
    return {"key": key, "value": encoded}


def decode(attributes):
    def value(item):
        if "arrayValue" in item:
            return [value(x) for x in item["arrayValue"].get("values", [])]
        if "intValue" in item:
            return int(item["intValue"])
        return next(iter(item.values()))
    return {item["key"]: value(item["value"]) for item in attributes}


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def run(image):
    captured = queue.Queue()

    class Capture(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            captured.put(json.loads(data))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    receiver_port, ui_port = unused_port(), unused_port()
    source = (REPO / "hosts/server-1/observability/alloy-config.alloy").read_text()
    start = source.index('otelcol.processor.transform "langfuse" {')
    end = source.index("\n}\n", start) + 3
    transform = source[start:end]
    first = transform.replace("otelcol.processor.filter.codex_noise.input",
                              "otelcol.processor.transform.langfuse_again.input")
    second = transform.replace('transform "langfuse"', 'transform "langfuse_again"')
    second = second.replace("otelcol.processor.filter.codex_noise.input",
                            "otelcol.exporter.otlphttp.capture.input")
    config = f'''
logging {{ level = "warn" }}
otelcol.receiver.otlp "test" {{
    http {{ endpoint = "127.0.0.1:{receiver_port}" }}
    output {{ traces = [otelcol.processor.transform.langfuse.input] }}
}}
{first}
{second}
otelcol.exporter.otlphttp "capture" {{
    encoding = "json"
    client {{ endpoint = "http://127.0.0.1:{server.server_port}" }}
}}
'''
    # service, span attributes, resource extras, expected harness, expected tags
    cases = [
        ("claude-code", {}, {}, "claude-code", ["harness:claude-code"]),
        ("codex_cli_rs", {}, {}, "codex", ["harness:codex"]),
        ("codex_exec", {}, {}, "codex", ["harness:codex"]),
        ("pi", {}, {}, "pi", ["harness:pi"]),
        ("unknown-agent", {}, {}, None, None),
        ("codex_exec_extra", {}, {}, None, None),
        ("pi", {TAGS: ["application:test", "harness:pi"]}, {}, "pi",
         ["application:test", "harness:pi"]),
        ("claude-code", {TAGS: ["workflow:test"]}, {}, "claude-code",
         ["workflow:test", "harness:claude-code"]),
        ("pi", {HARNESS: "custom", "harness": "ignored"}, {}, "custom", ["harness:custom"]),
        ("pi", {"harness": "explicit-span"}, {"harness": "ignored"},
         "explicit-span", ["harness:explicit-span"]),
        ("pi", {}, {HARNESS: "explicit-resource", "harness": "ignored"},
         "explicit-resource", ["harness:explicit-resource"]),
        ("pi", {}, {"harness": "resource-harness"}, "resource-harness", ["harness:resource-harness"]),
        ("unknown-agent", {HARNESS: "explicit"}, {}, "explicit", ["harness:explicit"]),
        ("unknown-agent", {TAGS: ["keep-me"]}, {}, None, ["keep-me"]),
        ("pi", {TAGS: "legacy-tag"}, {}, "pi", ["legacy-tag", "harness:pi"]),
        ("pi", {TAGS: "harness:pi"}, {}, "pi", ["harness:pi"]),
        ("pi", {HARNESS: ""}, {}, "", None),
        (None, {}, {}, None, None),
        # Upstream Condenseit sends only traces. Keep its application identity,
        # model/usage and content without inventing harness/execution metadata.
        ("condenseit", {
            "application": "condenseit", "workflow": "digest",
            "langfuse.trace.metadata.application": "condenseit",
            "langfuse.environment": "acceptance",
            TAGS: ["application:condenseit"],
            "gen_ai.request.model": "requested-model",
            "gen_ai.response.model": "returned-model",
            "langfuse.observation.model.name": "returned-model",
            "gen_ai.usage.input_tokens": 200,
            "gen_ai.usage.output_tokens": 40,
            "langfuse.observation.usage_details": '{"input":140,"input_cached":60,"output":40,"total":240}',
            "langfuse.observation.output": "test reply",
        }, {"deployment.environment.name": "acceptance"}, None, ["application:condenseit"]),
    ]
    expected, resources = {}, []
    now = time.time_ns()
    for host in ("test-host-a", "test-host-b"):
        for index, (service, attrs, extra, harness, tags) in enumerate(cases):
            resource = {"host.name": host, **extra}
            if service is not None:
                resource["service.name"] = service
            trace_id, parent = secrets.token_hex(16), secrets.token_hex(8)
            spans = []
            for depth in range(2):
                span_id = parent if depth == 0 else secrets.token_hex(8)
                marker = f"{host}-{index}-{depth}"
                span_attrs = {"test.case": marker, "langfuse.session.id": f"test-{host}-{index}",
                              "application": "test-app", "workflow": "test-workflow",
                              "langfuse.observation.input": "test input",
                              **attrs}
                if service == "condenseit":
                    span_attrs["langfuse.observation.type"] = "generation" if depth else "agent"
                else:
                    span_attrs["execution_mode"] = "test-mode"
                span = {"traceId": trace_id, "spanId": span_id, "name": "tool normalization-test",
                        "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 1000000),
                        "attributes": [attribute(k, v) for k, v in span_attrs.items()]}
                if depth:
                    span["parentSpanId"] = parent
                spans.append(span)
                expected[marker] = (resource, span_attrs, harness, tags, trace_id, span_id, parent if depth else None)
            resources.append({"resource": {"attributes": [attribute(k, v) for k, v in resource.items()]},
                              "scopeSpans": [{"scope": {"name": "normalization-test"}, "spans": spans}]})

    name = "alloy-harness-test-" + secrets.token_hex(4)
    try:
        with tempfile.TemporaryDirectory(prefix="alloy-harness-test-") as directory:
            config_path = Path(directory) / "config.alloy"
            config_path.write_text(config)
            subprocess.run(["docker", "run", "--detach", "--rm", "--name", name,
                            "--network", "host", "--read-only", "--tmpfs", "/tmp",
                            "--mount", f"type=bind,src={config_path},dst=/etc/alloy/config.alloy,readonly",
                            image, "run", "--disable-reporting", "--storage.path=/tmp/alloy",
                            f"--server.http.listen-addr=127.0.0.1:{ui_port}", "/etc/alloy/config.alloy"],
                           check=True, capture_output=True, text=True)
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/-/ready", timeout=1):
                        break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Test Alloy did not become ready")
                    time.sleep(0.2)
            request = urllib.request.Request(f"http://127.0.0.1:{receiver_port}/v1/traces",
                                             data=json.dumps({"resourceSpans": resources}).encode(),
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200
                assert not json.load(response).get("partialSuccess")
            seen = set()
            while len(seen) < len(expected):
                for resource_spans in captured.get(timeout=15)["resourceSpans"]:
                    actual_resource = decode(resource_spans["resource"]["attributes"])
                    for scope in resource_spans["scopeSpans"]:
                        for span in scope["spans"]:
                            actual = decode(span["attributes"])
                            marker = actual["test.case"]
                            resource, original, harness, tags, trace_id, span_id, parent = expected[marker]
                            assert actual_resource == resource, marker
                            for key, value in original.items():
                                if key != TAGS:
                                    assert actual[key] == value, (marker, key)
                            assert actual.get(HARNESS) == harness, (marker, actual)
                            assert actual.get(TAGS) == tags, (marker, actual)
                            assert actual["langfuse.user.id"] == resource["host.name"], marker
                            assert span["traceId"] == trace_id and span["spanId"] == span_id, marker
                            assert span.get("parentSpanId") == parent, marker
                            assert not any(k.startswith("langfuse.trace.metadata.") and k != HARNESS and k not in original
                                           for k in actual), marker
                            seen.add(marker)
            logs = subprocess.check_output(["docker", "logs", name], stderr=subprocess.STDOUT, text=True)
            assert 'level=warn' not in logs and 'level=error' not in logs, logs
            print(f"PASS: {len(cases)} cases × 2 hosts × root/child = {len(seen)} spans; "
                  "two transform passes, no warnings/errors")
    except Exception:
        subprocess.run(["docker", "logs", "--tail", "40", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "stop", "--time", "5", name], capture_output=True, check=False)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Alloy image; defaults to the tracked Compose pin")
    args = parser.parse_args()
    if args.image is None:
        import yaml
        stack = yaml.safe_load((REPO / "hosts/server-1/observability/compose.yaml").read_text())
        args.image = stack["services"]["alloy"]["image"]
    run(args.image)
