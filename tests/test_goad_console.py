import cmd
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock


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

    def test_successful_load_does_not_stop_command_loop(self):
        stop = self.console.onecmd('load example-instance')

        self.assertIsNone(stop)
        self.console.lab_manager.load_instance.assert_called_once_with('example-instance')

    def test_load_instance_reports_success_for_non_interactive_cli(self):
        loaded = self.console.load_instance('example-instance')

        self.assertTrue(loaded)


if __name__ == '__main__':
    unittest.main()
