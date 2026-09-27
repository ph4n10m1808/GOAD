import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSION = PROJECT_ROOT / "extensions" / "splunk_uf"


class SplunkUniversalForwarderExtensionTest(unittest.TestCase):
    def test_extension_is_agent_only(self):
        metadata = json.loads((EXTENSION / "extension.json").read_text())
        self.assertEqual(metadata["name"], "splunk_uf")
        self.assertEqual(metadata["machines"], [])
        self.assertIn("*", metadata["compatibility"])

    def test_inventory_targets_domain_machines(self):
        inventory = (EXTENSION / "inventory").read_text()
        self.assertIn("[splunk_uf_targets:children]", inventory)
        self.assertIn("domain", inventory)

    def test_direct_forwarding_has_no_deployment_server(self):
        outputs = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_uf_windows"
            / "templates"
            / "outputs.conf.j2"
        ).read_text()
        tasks = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_uf_windows"
            / "tasks"
            / "main.yml"
        ).read_text()
        self.assertIn("[tcpout]", outputs)
        self.assertIn("splunk_uf_receiver_host", outputs)
        self.assertNotIn("DEPLOYMENT_SERVER", tasks)
        self.assertNotIn("deploymentclient.conf", tasks)

    def test_collects_sysmon_and_security_logs(self):
        inputs = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_uf_windows"
            / "templates"
            / "inputs.conf.j2"
        ).read_text()
        self.assertIn("[WinEventLog://Security]", inputs)
        self.assertIn(
            "[WinEventLog://Microsoft-Windows-Sysmon/Operational]", inputs
        )

    def test_recovery_credentials_are_documented(self):
        defaults = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_uf_windows"
            / "defaults"
            / "main.yml"
        ).read_text()
        readme = (EXTENSION / "README.md").read_text()
        self.assertIn('splunk_uf_admin_user: "splunk"', defaults)
        self.assertIn("GOAD_SPLUNK_UF_PASSWORD", defaults)
        self.assertNotIn("Splunk@@", defaults)
        self.assertIn("SplunkForwarder (LocalSystem)", readme)

    def test_indexer_bootstrap_uses_environment_and_rest_api(self):
        install = (EXTENSION / "ansible" / "install.yml").read_text()
        defaults = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_indexer_bootstrap"
            / "defaults"
            / "main.yml"
        ).read_text()
        configure = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_indexer_bootstrap"
            / "tasks"
            / "configure.yml"
        ).read_text()
        self.assertIn("role: splunk_indexer_bootstrap", install)
        self.assertIn("GOAD_SPLUNK_INDEXER_PASSWORD", defaults)
        self.assertIn("GOAD_SPLUNK_RECEIVER_PORT", defaults)
        self.assertIn("/data/indexes", configure)
        self.assertIn("/data/inputs/tcp/cooked", configure)
        self.assertIn("no_log: true", configure)

    def test_forwarder_and_indexer_share_environment_names(self):
        indexer_defaults = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_indexer_bootstrap"
            / "defaults"
            / "main.yml"
        ).read_text()
        forwarder_defaults = (
            EXTENSION
            / "ansible"
            / "roles"
            / "splunk_uf_windows"
            / "defaults"
            / "main.yml"
        ).read_text()
        for variable in (
            "GOAD_SPLUNK_RECEIVER_PORT",
            "GOAD_SPLUNK_WINDOWS_INDEX",
            "GOAD_SPLUNK_WEB_INDEX",
        ):
            self.assertIn(variable, indexer_defaults)
            self.assertIn(variable, forwarder_defaults)


if __name__ == "__main__":
    unittest.main()
