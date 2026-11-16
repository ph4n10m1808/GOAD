"""Focused regressions for native RSAT completion and gMSA prerequisite failures."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from jinja2 import Environment
import yaml

TASKS = yaml.safe_load((Path(__file__).resolve().parents[1] / 'ansible/roles/gmsa_hosts/tasks/main.yml').read_text())


class FeatureRebootGateTest(unittest.TestCase):
    def evaluate(self, reboot, pending, selected=True):
        condition = next(t['when'] for t in TASKS if 'ansible.windows.win_reboot' in t)
        registered = {'reboot_required': reboot} if selected else {'skipped': True}
        state = {'result': {'install_pending': pending}} if selected else {'skipped': True}
        return Environment().from_string('{{ ' + condition + ' }}').render(
            gmsa_ad_tools={'results': [registered]}, gmsa_ad_tools_state={'results': [state]}) == 'True'

    def test_install_requests_reboot(self):
        self.assertTrue(self.evaluate(True, False))

    def test_pending_rerun_without_native_reboot_flag(self):
        self.assertTrue(self.evaluate(False, True))

    def test_ready_host_does_not_reboot(self):
        self.assertFalse(self.evaluate(False, False))

    def test_unselected_host_does_not_reboot(self):
        self.assertFalse(self.evaluate(True, True, selected=False))


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is required')
class GmsaHostPrerequisiteTest(unittest.TestCase):
    def run_script(self, setup=''):
        task = next(t for t in TASKS if t['name'].startswith('Install and verify gMSA'))
        harness = r'''
$Ansible=@{Changed=$true;Result=@{}}
$script:State='Installed';$script:ModuleMissing=$false;$script:Ready=$true;$script:Verified=$true
$script:Tests=0;$script:Installs=0;$script:TestError=$null;$script:InstallError=$null
function Import-Module { [CmdletBinding()] param($Name)
 if($Name -eq 'ActiveDirectory' -and $script:ModuleMissing){throw [System.IO.FileNotFoundException]::new('AD module missing')}
}
function Get-WindowsFeature { [CmdletBinding()] param($Name)
 [pscustomobject]@{Name=$Name;Installed=($script:State -ne 'Available');InstallState=$script:State}
}
function Test-ADServiceAccount { [CmdletBinding()] param($Identity)
 $script:Tests++
 if($script:TestError){throw $script:TestError}
 if($script:Tests -eq 1){$script:Ready}else{$script:Verified}
}
function Install-ADServiceAccount { [CmdletBinding()] param($Identity)
 $script:Installs++
 if($script:InstallError){throw $script:InstallError}
}
''' + setup + '\n$body={\n' + task['ansible.windows.win_powershell']['script'] + '\n}\n'
        harness += r'''
$failure=$null
try {$output=& $body -gMSA_Name 'gmsaDragon'}catch{$failure=$_.Exception.Message}
[pscustomobject]@{failure=$failure;result=$Ansible.Result;changed=$Ansible.Changed;tests=$script:Tests;installs=$script:Installs;output=$output} | ConvertTo-Json -Compress -Depth 5
'''
        with tempfile.TemporaryDirectory(prefix='gmsa-host-regression-') as directory:
            script = Path(directory) / 'check.ps1'
            script.write_text(harness)
            result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(script)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_pending_feature_fails_before_gmsa_and_is_not_retried(self):
        result = self.run_script("$script:State='InstallPending'")
        self.assertIn('installation is incomplete', result['failure'])
        self.assertEqual((result['tests'], result['installs']), (0, 0))
        self.assertFalse(result['changed'])
        self.assertFalse(result['result']['retryable'])

    def test_missing_feature_fails_before_gmsa(self):
        result = self.run_script("$script:State='Available'")
        self.assertIn('installation is incomplete', result['failure'])
        self.assertFalse(result['result']['retryable'])

    def test_missing_module_is_not_a_replication_retry(self):
        result = self.run_script('$script:ModuleMissing=$true')
        self.assertEqual(result['failure'], 'AD module missing')
        self.assertFalse(result['changed'])
        self.assertFalse(result['result']['retryable'])
        self.assertEqual(result['tests'], 0)

    def test_ready_account_does_not_install_or_change_service_identity(self):
        result = self.run_script()
        self.assertIsNone(result['failure'])
        self.assertFalse(result['changed'])
        self.assertEqual(result['installs'], 0)
        self.assertFalse(result['output']['service_identity_changed'])

    def test_install_once_then_verify(self):
        result = self.run_script('$script:Ready=$false')
        self.assertIsNone(result['failure'])
        self.assertTrue(result['changed'])
        self.assertEqual((result['tests'], result['installs']), (2, 1))

    def test_not_yet_usable_account_retries(self):
        result = self.run_script('$script:Ready=$false;$script:Verified=$false')
        self.assertIn('cannot yet use', result['failure'])
        self.assertTrue(result['result']['retryable'])

    def test_authorization_failure_does_not_retry(self):
        result = self.run_script("$script:TestError=[System.UnauthorizedAccessException]::new('Denied')")
        self.assertEqual(result['failure'], 'Denied')
        self.assertFalse(result['result']['retryable'])
        self.assertFalse(result['changed'])

    def test_missing_account_is_a_bounded_replication_retry(self):
        result = self.run_script('''
Add-Type 'namespace Microsoft.ActiveDirectory.Management {public class ADIdentityNotFoundException:System.Exception{public ADIdentityNotFoundException(string s):base(s){}}}'
$script:TestError=[Microsoft.ActiveDirectory.Management.ADIdentityNotFoundException]::new('Account not replicated')
''')
        self.assertEqual(result['failure'], 'Account not replicated')
        self.assertTrue(result['result']['retryable'])
        self.assertEqual(result['installs'], 0)


if __name__ == '__main__':
    unittest.main()
