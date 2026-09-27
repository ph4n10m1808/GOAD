import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSMON_EXTENSION = PROJECT_ROOT / "extensions" / "sysmon"


class SysmonExtensionTest(unittest.TestCase):
    def test_extension_is_agent_only_and_compatible_with_all_labs(self):
        metadata = json.loads((SYSMON_EXTENSION / "extension.json").read_text())
        self.assertEqual(metadata["name"], "sysmon")
        self.assertEqual(metadata["machines"], [])
        self.assertIn("*", metadata["compatibility"])

    def test_inventory_targets_the_domain_group(self):
        inventory = (SYSMON_EXTENSION / "inventory").read_text()
        self.assertIn("[sysmon_targets:children]", inventory)
        self.assertIn("domain", inventory)

    def test_install_and_uninstall_playbooks_exist(self):
        self.assertTrue((SYSMON_EXTENSION / "ansible" / "install.yml").is_file())
        self.assertTrue((SYSMON_EXTENSION / "ansible" / "uninstall.yml").is_file())

    def test_splunk_input_collects_the_sysmon_channel(self):
        inputs = (
            SYSMON_EXTENSION / "examples" / "splunk" / "inputs.conf"
        ).read_text()
        self.assertIn(
            "[WinEventLog://Microsoft-Windows-Sysmon/Operational]", inputs
        )
        self.assertIn("renderXml = true", inputs)

    def test_elk_no_longer_installs_sysmon(self):
        elk_tasks = (
            PROJECT_ROOT
            / "extensions"
            / "elk"
            / "ansible"
            / "roles"
            / "logs_windows"
            / "tasks"
            / "main.yml"
        ).read_text()
        self.assertNotIn("-accepteula -i", elk_tasks)
        self.assertNotIn("sysmon64.exe", elk_tasks.lower())


if __name__ == "__main__":
    unittest.main()
