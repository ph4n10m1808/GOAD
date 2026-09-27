import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from goad.elastic_agent import configure, fleet_url, parse_options, validate_ca, ConfigurationError


ROOT = Path(__file__).resolve().parents[1]


class ElasticAgentConfigurationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert_directory = tempfile.TemporaryDirectory()
        cls.crt = Path(cls.cert_directory.name) / 'test CA.crt'
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-subj', '/CN=GOAD test CA', '-addext', 'basicConstraints=critical,CA:TRUE',
            '-keyout', str(Path(cls.cert_directory.name) / 'test.key'),
            '-out', str(cls.crt),
        ], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.cert_directory.cleanup()

    def test_urls(self):
        for supplied, expected in (
            ('192.168.50.10', 'https://192.168.50.10:8220'),
            ('fleet.example.com:443', 'https://fleet.example.com:443'),
            ('https://fleet.example.com:8220/', 'https://fleet.example.com:8220'),
            ('[::1]', 'https://[::1]:8220'),
        ):
            with self.subTest(supplied=supplied):
                self.assertEqual(fleet_url(supplied), expected)
        for supplied in ('', '999.1.1.1', 'http://host', 'https://user:pass@host',
                         'https://host/path', 'host:0', 'host:65536', 'host?secret=x',
                         'host;whoami', 'https://host/#fragment'):
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                fleet_url(supplied)

    def test_command_accepts_quoted_crt_path(self):
        options = parse_options('elastic_agent --ip 192.168.50.10 --token TEST== --crt "/tmp/my CA.crt" --version 8.19.16')
        self.assertEqual(options['crt'], '/tmp/my CA.crt')
        self.assertEqual(options['token'], 'TEST==')
        for command in ('elastic_agent --unknown SECRET', 'elastic_agent --crt "SECRET', 'elastic_agent --token'):
            with self.assertRaises(ConfigurationError) as raised:
                parse_options(command)
            self.assertNotIn('SECRET', str(raised.exception))

    def test_ca_rejects_invalid_pem_and_private_keys(self):
        validate_ca(self.crt.read_text())
        for pem in ('', '-----BEGIN CERTIFICATE-----\ninvalid',
                    self.crt.read_text() + '\n-----BEGIN PRIVATE KEY-----'):
            with self.assertRaises(ConfigurationError):
                validate_ca(pem)

    def test_config_roundtrip_permissions_and_no_prompts_when_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            options = dict(ip='192.168.50.10', token='TEST==', crt=str(self.crt), version='8.19.16')
            with patch('builtins.input', side_effect=AssertionError('unexpected prompt')):
                configure(directory, options=options)
                configure(directory)
            path = Path(directory) / 'elastic_agent_options.yml'
            values = json.loads(path.read_text())['all']['children']['elastic_agent_targets']['vars']
            self.assertEqual(values['elastic_agent_ca_pem'], self.crt.read_text())
            self.assertFalse(values['elastic_agent_insecure'])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            before = path.read_bytes()
            with self.assertRaises(ConfigurationError):
                configure(directory, force=True, options={**options, 'crt': '/missing/file.crt'})
            self.assertEqual(path.read_bytes(), before)

    def test_interactive_defaults_and_saved_ca_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('builtins.input', side_effect=['192.168.50.10', '', str(self.crt)]), patch('getpass.getpass', return_value='TEST=='):
                configure(directory)
            with patch('builtins.input', side_effect=['', '', '']), patch('getpass.getpass', return_value=''):
                configure(directory, force=True)
            values = json.loads((Path(directory) / 'elastic_agent_options.yml').read_text())['all']['children']['elastic_agent_targets']['vars']
            self.assertEqual(values['elastic_agent_version'], '8.19.16')
            self.assertEqual(values['elastic_agent_enrollment_token'], 'TEST==')
            self.assertEqual(values['elastic_agent_ca_pem'], self.crt.read_text())

    def test_ansible_inventory_receives_options_and_ca(self):
        executable = Path(sys.executable).parent / 'ansible-inventory'
        if not executable.exists():
            self.skipTest('ansible-inventory is not installed next to Python')
        with tempfile.TemporaryDirectory() as directory:
            configure(directory, options=dict(ip='192.168.50.10', token='TEST==', crt=str(self.crt), version='8.19.16'))
            result = subprocess.run([
                str(executable), '-i', str(ROOT / 'ad/GOAD/data/inventory'),
                '-i', str(ROOT / 'extensions/elastic_agent/inventory'),
                '-i', str(Path(directory) / 'elastic_agent_options.yml'), '--list',
            ], check=True, capture_output=True, text=True,
                env={**os.environ, 'ANSIBLE_LOCAL_TEMP': directory})
            inventory = json.loads(result.stdout)
            self.assertEqual(inventory['elastic_agent_targets']['children'], ['domain'])
            self.assertTrue(inventory['_meta']['hostvars'])
            for hostvars in inventory['_meta']['hostvars'].values():
                if 'elastic_agent_fleet_url' in hostvars:
                    self.assertEqual(hostvars['elastic_agent_fleet_url'], 'https://192.168.50.10:8220')
                    self.assertEqual(hostvars['elastic_agent_ca_pem'], self.crt.read_text())
                    self.assertEqual(hostvars['elastic_agent_enrollment_token'], 'TEST==')

    def test_health_check_rejects_unhealthy_agent_and_fleet(self):
        try:
            import yaml
            from ansible.parsing.dataloader import DataLoader
            from ansible.playbook.conditional import Conditional
            from ansible.template import Templar
        except ImportError:
            self.skipTest('Ansible is required to evaluate playbook conditions')
        tasks = yaml.safe_load((ROOT / 'extensions/elastic_agent/ansible/roles/elastic_agent_windows/tasks/main.yml').read_text())
        health = next(task for task in tasks if task['name'] == 'Verify local agent health')
        loader = DataLoader()
        for state, fleet_state, rc, expected in ((2, 2, 0, True), (3, 2, 0, False), (2, 0, 0, False), (2, 2, 1, False)):
            variables = {'elastic_agent_health': {'rc': rc, 'stdout': json.dumps({'state': state, 'FleetState': fleet_state})}}
            conditional = Conditional(loader=loader)
            conditional.when = health['until']
            with self.subTest(state=state, fleet_state=fleet_state, rc=rc):
                self.assertEqual(conditional.evaluate_conditional(Templar(loader=loader, variables=variables), variables), expected)


class ElasticAgentConsoleTest(unittest.TestCase):
    def console(self):
        tree = ast.parse((ROOT / 'goad.py').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Goad')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'do_install_extension')
        namespace = {'Log': Mock()}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<console test>', 'exec'), namespace)
        console = Mock()
        console._configure_elastic_agent.return_value = True
        console.lab_manager.get_current_instance_provider().install.return_value = True
        return namespace['do_install_extension'], console

    def test_configured_before_enable_and_invalid_config_stops_install(self):
        method, console = self.console()
        console._configure_elastic_agent.return_value = False
        self.assertFalse(method(console, 'elastic_agent --ip 192.168.50.10 --crt "/tmp/ca.crt"'))
        console.lab_manager.get_current_instance().enable_extension.assert_not_called()
        console.lab_manager.get_current_instance_provider().install.assert_not_called()

    def test_options_do_not_become_extension_name(self):
        method, console = self.console()
        method(console, 'elastic_agent --ip 192.168.50.10 --crt /tmp/ca.crt')
        console.lab_manager.get_current_instance().enable_extension.assert_called_once_with('elastic_agent')
        console.do_provision_extension.assert_called_once_with('elastic_agent')

    def test_other_extensions_keep_existing_flow(self):
        method, console = self.console()
        method(console, 'sysmon')
        console.lab_manager.get_current_instance().enable_extension.assert_called_once_with('sysmon')


if __name__ == '__main__':
    unittest.main()
