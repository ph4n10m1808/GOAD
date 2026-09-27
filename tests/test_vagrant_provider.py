import unittest
import sys
import types

sys.modules.setdefault("psutil", types.ModuleType("psutil"))
from goad.provider.vagrant.vagrant import VagrantProvider


class FakeCommand:
    def __init__(self):
        self.calls = []

    def run_vagrant(self, args, path):
        self.calls.append((args, path))
        return True


class VagrantProviderInstallTest(unittest.TestCase):
    def test_install_runs_vagrant_up(self):
        provider = VagrantProvider("GOAD-Light")
        provider.command = FakeCommand()
        provider.path = "/tmp/goad-provider"

        self.assertTrue(provider.install())

        self.assertEqual(provider.command.calls, [(["up"], "/tmp/goad-provider")])


if __name__ == "__main__":
    unittest.main()
