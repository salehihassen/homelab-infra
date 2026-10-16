"""Guard the query shape responsible for dropping severity-less API errors.

The live LogQL acceptance test is the behavioral check; these fast tests keep
its proven Boolean grouping and the harness-success semantics explicit.
"""

import json
from pathlib import Path
import unittest
import yaml

REPO = Path(__file__).resolve().parents[2]


class AgentDashboardTests(unittest.TestCase):
    def setUp(self):
        dashboard = json.loads((REPO / "hosts/server-1/observability/grafana/"
                                "provisioning/dashboards/agents.json").read_text())
        self.panels = {panel["id"]: panel for panel in dashboard["panels"]}

    def test_api_errors_do_not_require_optional_severity_fields(self):
        query = self.panels[10]["targets"][0]["expr"]
        self.assertIn('event_name="api_error" or (success="false" or level=~"(?i)error")', query)

    def test_failure_counter_does_not_claim_to_count_shell_exit_codes(self):
        self.assertIn("harness", self.panels[3].get("description", ""))
        self.assertIn("exit", self.panels[3].get("description", ""))

    def test_stat_labels_survive_instant_query_reduction(self):
        # Loki instant numeric-multi frames have a generic "Value" field.
        # legendFormat alone does not survive Grafana's stat reduction path;
        # name the actual field, matching query ref IDs rather than field order.
        expected = {
            1: {"A": "Total cost"},
            2: {"A": "Claude Code", "B": "Codex", "C": "pi"},
            3: {"A": "All agents", "B": "Failed"},
        }
        for panel_id, names in expected.items():
            panel = self.panels[panel_id]
            self.assertEqual(panel["type"], "stat")
            self.assertEqual({t["refId"] for t in panel["targets"]}, set(names))
            for ref, name in names.items():
                with self.subTest(panel=panel_id, query=ref):
                    display_name = panel["fieldConfig"]["defaults"].get("displayName")
                    for override in panel["fieldConfig"]["overrides"]:
                        if override["matcher"] == {"id": "byFrameRefID", "options": ref}:
                            for prop in override["properties"]:
                                if prop["id"] == "displayName":
                                    display_name = prop["value"]
                    self.assertEqual(display_name, name)

    def test_grafana_alert_interval_matches_its_default_scheduler(self):
        provision = yaml.safe_load((REPO / "hosts/server-1/observability/grafana/"
                                    "provisioning/alerting/telemetry.yaml").read_text())
        for group in provision["groups"]:
            interval = group["interval"]
            self.assertTrue(interval.endswith("s"))
            self.assertGreater(int(interval[:-1]), 0)
            self.assertEqual(int(interval[:-1]) % 10, 0)

    def test_self_monitoring_and_rules_are_internal(self):
        stack = yaml.safe_load((REPO / "hosts/server-1/observability/compose.yaml").read_text())
        self.assertNotIn("ports", stack["services"]["prometheus"])
        mounts = stack["services"]["alloy"]["volumes"]
        disk_mounts = [mount for mount in mounts if ":/telemetry-disks/" in mount]
        self.assertEqual(len(disk_mounts), 5)
        self.assertTrue(all(mount.endswith(":ro") for mount in disk_mounts))

    def test_grafana_reserves_memory_for_datasource_processes(self):
        # This is a capacity-policy guard, not an OOM simulation. The behavioral
        # regression is repeated browser loading against an isolated Grafana.
        stack = yaml.safe_load((REPO / "hosts/server-1/observability/compose.yaml").read_text())
        grafana = stack["services"]["grafana"]
        self.assertEqual(grafana["mem_limit"], "1g")
        # The Go target is per process, not a budget for the whole container;
        # Grafana also launches separate Loki/Prometheus and other plugins.
        self.assertEqual(grafana["environment"].get("GOMEMLIMIT"), "256MiB")


if __name__ == "__main__":
    unittest.main()
