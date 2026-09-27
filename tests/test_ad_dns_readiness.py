import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ActiveDirectoryDnsReadinessTest(unittest.TestCase):
    def test_parent_dc_publishes_and_validates_dc_locator_records(self):
        tasks = (
            PROJECT_ROOT / "ansible/roles/domain_controller/tasks/main.yml"
        ).read_text()

        self.assertIn("Configure the domain controller to use its private DNS service", tasks)
        self.assertIn("nltest.exe /dsregdns", tasks)
        self.assertIn("ipconfig.exe /registerdns", tasks)
        self.assertIn('[domain_adapter, nat_adapter] | unique | list', tasks)
        self.assertIn('_ldap._tcp.dc._msdcs.{{ domain }}', tasks)
        self.assertIn("nltest.exe /dsgetdc:{{ domain }} /force", tasks)

    def test_child_dc_requires_real_dc_locator_health_before_promotion(self):
        tasks = (
            PROJECT_ROOT / "ansible/roles/child_domain/tasks/main.yml"
        ).read_text()

        for port in (88, 135, 389, 445):
            self.assertIn(f"    - {port}", tasks)
        self.assertIn('_ldap._tcp.dc._msdcs.{{ parent_domain }}', tasks)
        self.assertIn("nltest.exe /dsgetdc:{{ parent_domain }} /force", tasks)
        self.assertIn('[domain_adapter, nat_adapter] | unique | list', tasks)
        self.assertIn("Win32_ComputerSystem", tasks)
        self.assertIn("Verify child domain promotion completed", tasks)
        self.assertIn("Get-Service NTDS", tasks)
        self.assertIn("-Type SOA -Server 127.0.0.1", tasks)

    def test_all_windows_hosts_prefer_private_network_over_nat(self):
        tasks = (PROJECT_ROOT / "ansible/roles/common/tasks/main.yml").read_text()

        self.assertIn("Prefer the private adapter over the NAT adapter", tasks)
        self.assertIn("PrivateMetric: 10", tasks)
        self.assertIn("NatMetric: 100", tasks)
        self.assertIn("-AutomaticMetric Disabled", tasks)


if __name__ == "__main__":
    unittest.main()
