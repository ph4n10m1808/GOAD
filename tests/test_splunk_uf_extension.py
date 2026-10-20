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

    def test_receiver_success_requires_an_explicit_enabled_state(self):
        try:
            import yaml
            from ansible.parsing.dataloader import DataLoader
            from ansible.playbook.conditional import Conditional
            from ansible.template import Templar
        except ImportError:
            self.skipTest('Ansible is required to evaluate receiver assertions')
        tasks = yaml.safe_load((EXTENSION / 'ansible/roles/splunk_indexer_bootstrap/tasks/configure.yml').read_text())
        guard = next(task for task in tasks if task['name'] == 'Require an enabled cooked TCP receiver')
        verify = next(task for task in tasks if task['name'] == 'Verify the managed cooked TCP receiver')
        synchronized = next(task for task in tasks if task['name'] == 'Show synchronized Splunk destination')
        self.assertLess(tasks.index(verify), tasks.index(guard))
        self.assertLess(tasks.index(guard), tasks.index(synchronized))
        self.assertEqual(verify['ansible.builtin.uri']['status_code'], 200)
        self.assertNotIn('when', guard)
        loader = DataLoader()
        for entries, expected in (
            ([{'content': {'disabled': False}}], True),
            ([{'content': {'disabled': 0}}], True),
            ([{'content': {'disabled': '0'}}], True),
            ([{'content': {'disabled': True}}], False),
            ([{'content': {'disabled': '1'}}], False),
            ([{'content': {'disabled': 'unknown'}}], False),
            ([{'content': {}}], False),
            ([], False),
        ):
            variables = {'splunk_indexer_receiver_verified': {'json': {'entry': entries}}}
            conditional = Conditional(loader=loader)
            conditional.when = guard['ansible.builtin.assert']['that']
            with self.subTest(entries=entries):
                self.assertEqual(conditional.evaluate_conditional(Templar(loader=loader, variables=variables), variables), expected)

    def test_failed_receiver_verification_stops_required_bootstrap(self):
        try:
            import yaml
            from ansible.parsing.dataloader import DataLoader
            from ansible.playbook.conditional import Conditional
            from ansible.template import Templar
        except ImportError:
            self.skipTest('Ansible is required to evaluate bootstrap failure policy')
        tasks = yaml.safe_load((EXTENSION / 'ansible/roles/splunk_indexer_bootstrap/tasks/main.yml').read_text())
        block = next(task for task in tasks if 'rescue' in task)
        mandatory = next(task for task in block['rescue'] if 'ansible.builtin.fail' in task)
        optional = next(task for task in block['rescue'] if 'ansible.builtin.debug' in task)
        loader = DataLoader()
        for required in (True, False):
            variables = {'splunk_indexer_config_required': required}
            for task, expected in ((mandatory, required), (optional, not required)):
                conditional = Conditional(loader=loader)
                conditional.when = task['when']
                with self.subTest(required=required, task=task['name']):
                    self.assertEqual(conditional.evaluate_conditional(Templar(loader=loader, variables=variables), variables), expected)


if __name__ == "__main__":
    unittest.main()
