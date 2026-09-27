import unittest
from unittest.mock import patch

from goad.command.cmd import Command


class CommandResultTest(unittest.TestCase):
    def test_run_vagrant_returns_false_when_executable_is_missing(self):
        command = Command()
        command.vagrant_bin = "missing-vagrant"

        with patch("goad.command.cmd.subprocess.run", side_effect=FileNotFoundError()):
            self.assertFalse(command.run_vagrant(["up"], "."))

    def test_run_process_returns_none_when_executable_is_missing(self):
        command = Command()

        with patch("goad.command.cmd.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(command.run_process(["missing-command"]))


if __name__ == "__main__":
    unittest.main()
