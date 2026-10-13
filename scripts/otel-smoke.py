#!/usr/bin/env python3
"""End-to-end smoke test for the agent telemetry path (standard library only).

Sends one synthetic agent run over OTLP/HTTP JSON: a trace (run -> model call ->
tool call -> tool result -> final model call, plus a failing span), matching log
events and a counter. The payload includes Unicode, multiline and large tool
output, and fake secrets that the Alloy redaction must replace.

  OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.<zone> \
  OTEL_CREDENTIALS_FILE=~/.config/agent-otel/credentials \
  python3 scripts/otel-smoke.py [--host-name NAME]

The credentials file holds `user:password` for one OTLP writer account.
Check results: Langfuse session `otel-smoke-<timestamp>`, Loki
`{service_name="otel-smoke"}`, Prometheus `otel_smoke_runs_total`.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import time
import urllib.request

# Fake secrets in real formats: none of these is a working credential.
FAKE_SECRETS = {
    "anthropic": "sk-ant-api03-" + "A" * 40,
    "openai": "sk-proj-" + "B" * 40,
    "github": "ghp_" + "C" * 36,
    "aws": "AKIA" + "D" * 16,
    "env": "DATABASE_PASSWORD=hunter2hunter2",
    "url": "postgresql://app:s3cretpass@db:5432/app",
    "bearer": "Authorization: Bearer " + "e" * 32,
}


def attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def post(endpoint, path, body, auth):
    request = urllib.request.Request(
        endpoint.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": auth})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read().decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host-name", default=socket.gethostname())
    args = parser.parse_args()

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    credentials = Path(os.path.expanduser(
        os.environ.get("OTEL_CREDENTIALS_FILE", "~/.config/agent-otel/credentials")))
    if not endpoint or not credentials.is_file():
        sys.exit("Set OTEL_EXPORTER_OTLP_ENDPOINT and provide OTEL_CREDENTIALS_FILE")
    auth = "Basic " + base64.b64encode(credentials.read_text().strip().encode()).decode()

    session = f"otel-smoke-{time.strftime('%Y%m%dT%H%M%S')}"
    trace_id = secrets.token_hex(16)
    now = time.time_ns()
    ms = 1_000_000
    resource = {"attributes": [
        attr("service.name", "otel-smoke"), attr("service.version", "1"),
        attr("host.name", args.host_name), attr("agent.type", "synthetic"),
        attr("deployment.environment.name", "test")]}
    big_output = "\n".join(f"line {i}: résumé ✓ 日本語 {'x' * 60}" for i in range(3000))
    secret_dump = "\n".join(f"{k}: {v}" for k, v in FAKE_SECRETS.items())

    def span(name, span_id, parent, start, end, attributes, error=None):
        item = {"traceId": trace_id, "spanId": span_id, "name": name, "kind": 1,
                "startTimeUnixNano": str(now + start * ms), "endTimeUnixNano": str(now + end * ms),
                "attributes": [attr("session.id", session), attr("langfuse.session.id", session),
                               *attributes]}
        if parent:
            item["parentSpanId"] = parent
        if error:
            item["status"] = {"code": 2, "message": error}
        return item

    ids = {name: secrets.token_hex(8) for name in ("run", "model1", "tool", "result", "model2", "fail")}
    spans = [
        span("test-agent-run", ids["run"], None, 0, 900, [
            attr("langfuse.trace.name", "otel-smoke run"),
            attr("langfuse.observation.input", "Plan a picnic.\nCheck the weather first. ☀️"),
            attr("langfuse.observation.output", "Picnic is on: 21 °C and sunny.")]),
        span("model-call", ids["model1"], ids["run"], 10, 210, [
            attr("langfuse.observation.type", "generation"),
            attr("gen_ai.system", "synthetic"), attr("gen_ai.request.model", "test-model-1"),
            attr("gen_ai.usage.input_tokens", 120), attr("gen_ai.usage.output_tokens", 30),
            attr("langfuse.observation.input", "User: Plan a picnic. Check the weather first."),
            attr("langfuse.observation.output", "Calling weather_test(city=Toronto)")]),
        span("tool-call weather_test", ids["tool"], ids["run"], 220, 420, [
            attr("langfuse.observation.type", "tool"), attr("tool.name", "weather_test"),
            attr("langfuse.observation.input", json.dumps({"city": "Toronto", "debug": secret_dump})),
            attr("langfuse.observation.output", "21 °C, sunny")]),
        span("simulated-tool-result", ids["result"], ids["tool"], 300, 410, [
            attr("tool.output.bytes", len(big_output)),
            attr("langfuse.observation.output", big_output[:150_000])]),
        span("final-model-call", ids["model2"], ids["run"], 430, 880, [
            attr("langfuse.observation.type", "generation"),
            attr("gen_ai.system", "synthetic"), attr("gen_ai.request.model", "test-model-1"),
            attr("gen_ai.usage.input_tokens", 400), attr("gen_ai.usage.output_tokens", 50),
            attr("langfuse.observation.input", "Tool says 21 °C, sunny."),
            attr("langfuse.observation.output", "Picnic is on: 21 °C and sunny.")]),
        span("tool-call flaky_test", ids["fail"], ids["run"], 500, 520,
             [attr("langfuse.observation.type", "tool"), attr("tool.name", "flaky_test")],
             error="simulated tool failure"),
    ]
    traces = {"resourceSpans": [{"resource": resource, "scopeSpans": [
        {"scope": {"name": "otel-smoke"}, "spans": spans}]}]}

    def log(event, offset, attributes):
        return {"timeUnixNano": str(now + offset * ms), "severityText": "INFO",
                "body": {"stringValue": event}, "traceId": trace_id,
                "attributes": [attr("event.name", event), attr("session.id", session), *attributes]}

    logs = {"resourceLogs": [{"resource": resource, "scopeLogs": [{"scope": {"name": "otel-smoke"}, "logRecords": [
        log("otel_smoke.user_prompt", 0, [attr("prompt", "Plan a picnic. ☀️\nSecond line.")]),
        log("otel_smoke.tool_result", 420, [attr("tool_name", "weather_test"), attr("success", True),
                                           attr("tool_input", secret_dump)]),
        log("otel_smoke.tool_result", 520, [attr("tool_name", "flaky_test"), attr("success", False),
                                           attr("error", "simulated tool failure")]),
        log("otel_smoke.secret_body " + FAKE_SECRETS["openai"], 600, []),
    ]}]}]}

    metrics = {"resourceMetrics": [{"resource": resource, "scopeMetrics": [{"scope": {"name": "otel-smoke"}, "metrics": [
        {"name": "otel_smoke.runs", "unit": "1", "sum": {
            "aggregationTemporality": 1, "isMonotonic": True,
            "dataPoints": [{"asInt": "1", "startTimeUnixNano": str(now), "timeUnixNano": str(now + 900 * ms),
                            "attributes": [attr("outcome", "ok")]}]}},
    ]}]}]}

    for path, body in (("/v1/traces", traces), ("/v1/logs", logs), ("/v1/metrics", metrics)):
        status, text = post(endpoint, path, body, auth)
        print(f"{path}: HTTP {status} {text}")
    print(f"session={session} trace_id={trace_id} host={args.host_name}")


if __name__ == "__main__":
    main()
