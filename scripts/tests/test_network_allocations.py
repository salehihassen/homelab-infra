import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "check_networks", Path(__file__).resolve().parents[1] / "check-networks.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def deployment(dynamic_range=None):
    allocation = {"subnet": "192.0.2.0/24"}
    if dynamic_range is not None:
        allocation["ip_range"] = dynamic_range
    return {
        "networks": {"shared": {"ipam": {"config": [allocation]}}},
        "services": {
            "gateway": {"networks": {"shared": {"ipv4_address": "192.0.2.2"}}},
            "client": {"networks": {"shared": None}},
        },
    }


class NetworkAllocationsTest(unittest.TestCase):
    def test_dynamic_client_cannot_take_gateway_address_on_restart(self):
        errors = checker.check(deployment())
        self.assertTrue(any("ip_range" in error for error in errors), errors)

    def test_static_address_inside_dynamic_pool_is_rejected(self):
        errors = checker.check(deployment("192.0.2.0/25"))
        self.assertTrue(any("dynamic" in error for error in errors), errors)

    def test_separate_dynamic_pool_is_valid(self):
        self.assertEqual(checker.check(deployment("192.0.2.128/25")), [])

    def test_dynamic_range_must_be_inside_subnet(self):
        errors = checker.check(deployment("198.51.100.0/24"))
        self.assertTrue(any("outside" in error for error in errors), errors)

    def test_duplicate_static_addresses_are_rejected(self):
        config = deployment("192.0.2.128/25")
        config["services"]["client"]["networks"]["shared"] = {
            "ipv4_address": "192.0.2.2"
        }
        errors = checker.check(config)
        self.assertTrue(any("duplicate" in error for error in errors), errors)

    def test_all_static_network_does_not_require_dynamic_range(self):
        config = deployment()
        del config["services"]["client"]
        self.assertEqual(checker.check(config), [])


if __name__ == "__main__":
    unittest.main()
