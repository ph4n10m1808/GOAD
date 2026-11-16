"""Offline regressions for the production LAPS schema wait, never guest commands."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is required')
class LapsSchemaWaitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'ansible/roles/laps/dc/tasks/install.yml'
        cls.task = next(task for task in yaml.safe_load(path.read_text())
                        if task['name'].startswith('Wait for LAPS schema visibility'))
        cls.script = cls.task['ansible.windows.win_powershell']['script']

    def run_script(self, setup=''):
        harness = r'''
$env:COMPUTERNAME='DC02'
$Ansible=@{Changed=$false;Result=@{}}
$script:Queries=0
$script:Refreshes=0
$script:Disposed=$false
$script:Root=[pscustomobject]@{schemaNamingContext='CN=Schema,CN=Configuration,DC=lab,DC=test';dnsHostName='dc02.child.lab.test'}
$id=[guid]'11111111-2222-3333-4444-555555555555'
$script:Attrs=@{
 '(lDAPDisplayName=ms-Mcs-AdmPwd)'=[pscustomobject]@{schemaIDGUID=$id.ToByteArray()}
 '(lDAPDisplayName=ms-Mcs-AdmPwdExpirationTime)'=[pscustomobject]@{schemaIDGUID=$id.ToByteArray()}
}
$script:Computers=[pscustomobject]@{mayContain=@('ms-Mcs-AdmPwd','ms-Mcs-AdmPwdExpirationTime')}
$script:ImportFailure=$false
$script:ReadFailure=$false
$script:RefreshFailure=$false
function Import-Module {
 [CmdletBinding()]param($Name)
 if ($script:ImportFailure) { throw 'Native module missing' }
}
function Get-ADRootDSE {
 [CmdletBinding()]param($Server)
 if ($Server -ne 'DC02') { throw 'Wrong DC' }
 $script:Root
}
function Get-ADObject {
 [CmdletBinding()]param($Server,$SearchBase,$LDAPFilter,$Properties,$Identity)
 $script:Queries++
 if ($Server -ne 'DC02') { throw 'Wrong DC' }
 if ($script:ReadFailure) { throw 'Directory access denied' }
 if ($Identity) {
  if ($Properties -ne 'mayContain') { throw 'Wrong Computer properties' }
  Write-Output -NoEnumerate $script:Computers
 } else {
  if ($Properties -ne 'schemaIDGUID' -or -not $script:Attrs.ContainsKey($LDAPFilter)) { throw 'Wrong attribute scope' }
  Write-Output -NoEnumerate $script:Attrs[$LDAPFilter]
 }
}
function New-Object {
 param($TypeName,$ArgumentList)
 if ($TypeName -ne 'System.DirectoryServices.DirectoryEntry' -or
     $ArgumentList[0] -ne 'LDAP://dc02.child.lab.test/RootDSE') { throw 'Wrong cache target' }
 $entry=[pscustomobject]@{}
 $entry | Add-Member ScriptMethod Put {param($Name,$Value) if ($Name -ne 'schemaUpdateNow' -or $Value -ne 1) { throw 'Wrong cache operation' }}
 $entry | Add-Member ScriptMethod SetInfo {if ($script:RefreshFailure) { throw 'Cache update access denied' };$script:Refreshes++}
 $entry | Add-Member ScriptMethod Dispose {$script:Disposed=$true}
 $entry
}
''' + setup + '\n$body={\n' + self.script + '\n}\n'
        harness += r'''
$failure=$null
try {$output=& $body} catch {$failure=$_.Exception.Message}
[pscustomobject]@{failure=$failure;result=$Ansible.Result;changed=$Ansible.Changed;
 output=$output;queries=$script:Queries;refreshes=$script:Refreshes;disposed=$script:Disposed} | ConvertTo-Json -Compress -Depth 6
'''
        with tempfile.TemporaryDirectory(prefix='laps-schema-wait-') as directory:
            path = Path(directory) / 'check.ps1'
            path.write_text(harness)
            result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(path)],
                                    capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_visible_schema_refreshes_exact_local_cache(self):
        result = self.run_script()
        self.assertIsNone(result['failure'])
        self.assertTrue(result['output']['schema_visible'])
        self.assertEqual(result['refreshes'], 1)
        self.assertTrue(result['disposed'])
        self.assertFalse(result['changed'])

    def test_non_enumerated_attribute_and_computer_lists_are_flattened(self):
        result = self.run_script(r'''
foreach ($name in @($script:Attrs.Keys)) {
 $list=[System.Collections.Generic.List[object]]::new();$list.Add($script:Attrs[$name]);$script:Attrs[$name]=$list
}
$list=[System.Collections.Generic.List[object]]::new();$list.Add($script:Computers);$script:Computers=$list
''')
        self.assertIsNone(result['failure'])
        self.assertTrue(result['output']['schema_visible'])

    def test_empty_attribute_list_retries_without_refresh(self):
        result = self.run_script("$script:Attrs['(lDAPDisplayName=ms-Mcs-AdmPwd)']=[System.Collections.Generic.List[object]]::new()")
        self.assertTrue(result['result']['retryable'])
        self.assertIn('not replicated', result['failure'])
        self.assertEqual(result['refreshes'], 0)

    def test_null_attribute_retries_without_null_method(self):
        result = self.run_script("$script:Attrs['(lDAPDisplayName=ms-Mcs-AdmPwd)']=$null")
        self.assertTrue(result['result']['retryable'])
        self.assertIn('not replicated', result['failure'])

    def test_missing_computer_membership_retries(self):
        result = self.run_script("$script:Computers=[pscustomobject]@{mayContain=@('ms-Mcs-AdmPwd')}")
        self.assertTrue(result['result']['retryable'])
        self.assertIn('not received', result['failure'])
        self.assertEqual(result['refreshes'], 0)

    def test_ambiguous_attribute_fails_immediately(self):
        result = self.run_script("$script:Attrs['(lDAPDisplayName=ms-Mcs-AdmPwd)']=@($script:Attrs['(lDAPDisplayName=ms-Mcs-AdmPwd)'],$script:Attrs['(lDAPDisplayName=ms-Mcs-AdmPwd)'])")
        self.assertFalse(result['result']['retryable'])
        self.assertIn('ambiguous', result['failure'])

    def test_missing_guid_is_not_called_replication(self):
        result = self.run_script("$script:Attrs['(lDAPDisplayName=ms-Mcs-AdmPwd)']=[pscustomobject]@{}")
        self.assertFalse(result['result']['retryable'])
        self.assertIn('invalid schemaIDGUID', result['failure'])

    def test_empty_guid_fails_immediately(self):
        result = self.run_script("$script:Attrs['(lDAPDisplayName=ms-Mcs-AdmPwd)']=[pscustomobject]@{schemaIDGUID=([guid]::Empty).ToByteArray()}")
        self.assertFalse(result['result']['retryable'])
        self.assertIn('empty schemaIDGUID', result['failure'])

    def test_invalid_root_metadata_fails_immediately(self):
        result = self.run_script("$script:Root=[pscustomobject]@{schemaNamingContext=''}")
        self.assertFalse(result['result']['retryable'])
        self.assertEqual(result['queries'], 0)

    def test_access_failure_is_not_retried(self):
        result = self.run_script('$script:ReadFailure=$true')
        self.assertFalse(result['result']['retryable'])
        self.assertIn('access denied', result['failure'])

    def test_cache_permission_failure_is_not_retried_and_disposes(self):
        result = self.run_script('$script:RefreshFailure=$true')
        self.assertFalse(result['result']['retryable'])
        self.assertTrue(result['disposed'])
        self.assertEqual(result['refreshes'], 0)

    def test_module_failure_does_not_retry_or_claim_changes(self):
        result = self.run_script('$script:ImportFailure=$true')
        self.assertFalse(result['result']['retryable'])
        self.assertFalse(result['changed'])
        self.assertEqual(result['queries'], 0)

    def test_only_true_replication_outcomes_repeat_in_ansible(self):
        from jinja2 import Environment
        env = Environment()
        env.tests['success'] = lambda value: not value.get('failed', False)
        env.filters['bool'] = bool
        expression = env.compile_expression(self.task['until'])
        self.assertFalse(expression(laps_schema_visibility={'failed': True, 'result': {'retryable': True}}))
        self.assertTrue(expression(laps_schema_visibility={'failed': True, 'result': {'retryable': False}}))
        self.assertTrue(expression(laps_schema_visibility={'failed': True}))
        self.assertTrue(expression(laps_schema_visibility={'failed': False}))
        self.assertEqual(self.task['vars']['ansible_become_user'], 'SYSTEM')
        self.assertNotIn('delegate_to', self.task)


if __name__ == '__main__':
    unittest.main()
