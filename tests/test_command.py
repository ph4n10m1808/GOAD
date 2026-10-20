import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from goad.command.cmd import Command
from goad.command.linux import LinuxCommand


class CommandResultTest(unittest.TestCase):
    def test_ansible_keeps_quoted_extra_vars_in_one_argument_without_shell(self):
        with patch("goad.command.cmd.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(Command().run_ansible('site.yml --extra-vars "label=SOC lab; literal"', "."))
        self.assertEqual(run.call_args.args[0], ["ansible-playbook", "site.yml", "--extra-vars", "label=SOC lab; literal"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_run_vagrant_returns_false_when_executable_is_missing(self):
        command = Command()
        command.vagrant_bin = "missing-vagrant"

        with patch("goad.command.cmd.subprocess.run", side_effect=FileNotFoundError()):
            self.assertFalse(command.run_vagrant(["up"], "."))

    def test_run_process_returns_none_when_executable_is_missing(self):
        command = Command()

        with patch("goad.command.cmd.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(command.run_process(["missing-command"]))

    def test_docker_ansible_preserves_selected_launcher_and_quoted_arguments(self):
        for launcher in (['docker'], ['pkexec', 'docker']):
            with self.subTest(launcher=launcher), patch(
                'goad.command.linux.subprocess.run', return_value=SimpleNamespace(returncode=0)
            ) as run:
                self.assertTrue(LinuxCommand().run_docker_ansible(
                    'site.yml --extra-vars "label=SOC lab; literal"', '.', '/goad/ansible', launcher,
                ))
            argv = run.call_args.args[0]
            self.assertEqual(argv[:len(launcher) + 1], launcher + ['run'])
            self.assertEqual(argv[-4:], ['ansible-playbook', 'site.yml', '--extra-vars', 'label=SOC lab; literal'])
            self.assertFalse(run.call_args.kwargs.get('shell', False))


class DockerProvisionerLauncherTest(unittest.TestCase):
    def setUp(self):
        try:
            from goad.provisioner.ansible.docker import DockerAnsibleProvisionerCmd
        except ImportError:
            self.skipTest('The Docker provisioner requires POSIX group support')
        self.provisioner_class = DockerAnsibleProvisionerCmd
        self.provider = SimpleNamespace(provider_name='libvirt')

    def provisioner(self, configured, in_group=False):
        environment = {} if configured is None else {'GOAD_DOCKER_LAUNCHER': configured}
        with patch.dict('os.environ', environment, clear=True), patch.object(
            self.provisioner_class, 'check_docker_image'
        ), patch.object(self.provisioner_class, 'is_current_user_in_docker_group', return_value=in_group) as group:
            provisioner = self.provisioner_class('GOAD-Light', self.provider)
        return provisioner, group

    def test_wrapper_launcher_is_reused_without_rechecking_login_identity(self):
        for configured, expected in (('docker', ['docker']), ('pkexec docker', ['pkexec', 'docker'])):
            with self.subTest(configured=configured):
                provisioner, group = self.provisioner(configured)
                self.assertEqual(provisioner.docker_command, expected)
                group.assert_not_called()

    def test_direct_python_invocation_uses_pkexec_outside_docker_group(self):
        for in_group, expected in ((True, ['docker']), (False, ['pkexec', 'docker'])):
            with self.subTest(in_group=in_group):
                provisioner, group = self.provisioner(None, in_group)
                self.assertEqual(provisioner.docker_command, expected)
                group.assert_called_once_with()

    def test_unrecognized_launcher_is_rejected(self):
        with self.assertRaises(ValueError):
            self.provisioner('sudo docker')

    def test_image_inspection_uses_the_same_launcher_without_a_shell_pipeline(self):
        provisioner, _ = self.provisioner('pkexec docker')
        provisioner.command = Mock()
        provisioner.command.run_process.return_value = SimpleNamespace(returncode=0)
        provisioner.check_docker_image()
        self.assertEqual(provisioner.command.run_process.call_args.args[0],
                         ['pkexec', 'docker', 'image', 'inspect', 'goadansible'])
        provisioner.command.run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
