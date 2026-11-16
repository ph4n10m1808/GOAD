import cmd
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('goad_cli', ROOT / 'goad.py')
GOAD_CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GOAD_CLI)
Goad = GOAD_CLI.Goad


class GoadConsoleTest(unittest.TestCase):
    def setUp(self):
        self.console = Goad.__new__(Goad)
        cmd.Cmd.__init__(self.console)
        self.console.lab_manager = Mock()
        self.console.lab_manager.load_instance.return_value = True
        self.console.lab_manager.current_instance = Mock()
        self.console.lab_manager.get_current_instance_id.return_value = 'example-instance'

    def test_extension_batch_runs_in_order_with_public_elastic_options(self):
        self.console.do_install_extension = Mock(return_value=True)
        ca = '/tmp/CA directory/public.crt'
        self.assertTrue(self.console.install_extensions(['sysmon', 'elastic_agent'],
            {'ip': 'https://fleet.example.com:8220', 'crt': ca, 'version': '9.5.6'}))
        calls = self.console.do_install_extension.call_args_list
        self.assertEqual(calls[0].args, ('sysmon',))
        import shlex
        self.assertEqual(shlex.split(calls[1].args[0]), ['elastic_agent', '--ip',
            'https://fleet.example.com:8220', '--crt', ca, '--version', '9.5.6'])

    def test_extension_batch_stops_on_failure(self):
        self.console.do_install_extension = Mock(side_effect=[False, True])
        self.assertFalse(self.console.install_extensions(['sysmon', 'elastic_agent']))
        self.console.do_install_extension.assert_called_once_with('sysmon')

    def test_extension_batch_validates_before_installation(self):
        self.console.do_install_extension = Mock(return_value=True)
        lab = self.console.lab_manager.get_current_instance_lab.return_value
        lab.get_extension.side_effect = lambda name: None if name == 'unknown' else Mock()
        for names in ([], ['sysmon', 'sysmon'], ['sysmon', 'unknown']):
            self.assertFalse(self.console.install_extensions(names))
        self.console.lab_manager.get_current_instance.return_value = None
        self.assertFalse(self.console.install_extensions(['sysmon']))
        self.console.do_install_extension.assert_not_called()

    def test_successful_load_does_not_stop_command_loop(self):
        stop = self.console.onecmd('load example-instance')

        self.assertIsNone(stop)
        self.console.lab_manager.load_instance.assert_called_once_with('example-instance')

    def test_load_instance_reports_success_for_non_interactive_cli(self):
        loaded = self.console.load_instance('example-instance')

        self.assertTrue(loaded)

    def prepare_uninstall(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.playbook = Path(temporary.name) / 'uninstall.yml'
        self.playbook.write_text('---\n[]\n')
        instance = self.console.lab_manager.get_current_instance.return_value
        instance.extensions = ['elastic_agent', 'sysmon']
        extension = self.console.lab_manager.get_current_instance_lab.return_value.get_extension.return_value
        extension.list_providers_name.return_value = []
        extension.get_ansible_path.return_value = temporary.name
        extension.get_playbook.return_value = 'uninstall.yml'
        provisioner = self.console.lab_manager.get_current_instance_provisioner.return_value
        provisioner.use_jumpbox = False
        provisioner.run_extension.return_value = True
        return instance, extension, provisioner

    def test_uninstall_disables_only_after_success_without_provider_lifecycle(self):
        instance, extension, provisioner = self.prepare_uninstall()
        provisioner.run_extension.side_effect = lambda *a, **kw: not instance.disable_extension.called
        self.assertTrue(self.console.uninstall_extension('elastic_agent'))
        provisioner.run_extension.assert_called_once_with(extension, ['elastic_agent', 'sysmon'], install=False)
        instance.disable_extension.assert_called_once_with('elastic_agent')
        self.console.lab_manager.get_current_instance_provider.assert_not_called()

    def test_failed_uninstall_keeps_extension_enabled(self):
        instance, _, provisioner = self.prepare_uninstall()
        provisioner.run_extension.return_value = False
        self.assertFalse(self.console.uninstall_extension('elastic_agent'))
        instance.disable_extension.assert_not_called()

    def test_unsupported_or_missing_uninstall_never_mutates_instance(self):
        for case in ('missing_playbook', 'provider_resources', 'remote', 'disabled', 'unknown'):
            with self.subTest(case=case):
                instance, extension, provisioner = self.prepare_uninstall()
                if case == 'missing_playbook':self.playbook.unlink()
                elif case == 'provider_resources':extension.list_providers_name.return_value = ['libvirt']
                elif case == 'remote':provisioner.use_jumpbox = True
                elif case == 'disabled':instance.extensions = ['sysmon']
                elif case == 'unknown':self.console.lab_manager.get_current_instance_lab.return_value.get_extension.return_value = None
                self.assertFalse(self.console.uninstall_extension('elastic_agent'))
                instance.disable_extension.assert_not_called()
                provisioner.run_extension.assert_not_called()
                self.console.lab_manager.reset_mock()

    def test_uninstall_requires_instance_and_single_extension_argument(self):
        self.console.lab_manager.get_current_instance.return_value = None
        for arg in ('', 'elastic_agent --force', 'elastic_agent'):
            self.assertFalse(self.console.uninstall_extension(arg))
        self.console.lab_manager.get_current_instance_provisioner.assert_not_called()

    def test_successful_uninstall_keeps_console_open(self):
        self.prepare_uninstall()
        self.assertIsNone(self.console.onecmd('uninstall_extension elastic_agent'))


if __name__ == '__main__':
    unittest.main()
