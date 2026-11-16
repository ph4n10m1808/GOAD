"""Offline execution of the production LAPS audit task with native-shaped ACLs."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is required')
class LapsAuditDriveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'ansible/roles/laps/permissions/tasks/main.yml'
        task = next(t for t in yaml.safe_load(path.read_text()) if t['name'].startswith('Audit legacy LAPS'))
        cls.script = task['ansible.windows.win_powershell']['script']
        # Windows identity types do not run on Linux; retain all task control
        # flow but use harmless stand-ins for only those identity conversions.
        cls.script = cls.script.replace("[System.Security.Principal.SecurityIdentifier]'S-1-1-0'", "([pscustomobject]@{Value='S-1-1-0'})")
        cls.script = cls.script.replace('$everyone.Translate([System.Security.Principal.NTAccount]).Value', "'Everyone'")

    def run_script(self, setup=''):
        harness = r'''
$env:COMPUTERNAME='DC02'
$Ansible=@{Changed=$false}
$script:Created=$false;$script:Removed=$false;$script:AuditCalls=0;$script:ReadCount=0
$script:FailCreate=$false;$script:FailModule=$false;$script:FailRead=$false;$script:FailAudit=$false
$script:ChangeDacl=$false;$script:NoRule=$false;$script:AlreadyAudited=$false;$script:MissingGuid=$false
$script:ExpectedOU='OU=Laps,DC=child,DC=lab,DC=test'
$script:SchemaId=[guid]'11111111-2222-3333-4444-555555555555'
function Import-Module {[CmdletBinding()]param($Name) if($script:FailModule){throw 'Module missing'}}
function Get-ADOrganizationalUnit {[CmdletBinding()]param($Identity,$Server) if($Server -ne 'DC02' -or $Identity -ne $script:ExpectedOU){throw 'Wrong OU/DC'};[pscustomobject]@{DistinguishedName=$Identity}}
function New-PSDrive {
 [CmdletBinding()]param($Name,$PSProvider,$Root,$Server,$Scope)
 if($script:FailCreate){throw 'Drive creation failed'}
 if($Name -notmatch '^SocLapsAudit_[0-9a-f]{32}$' -or $PSProvider -ne 'ActiveDirectory' -or $Root -ne '' -or $Server -ne 'DC02' -or $Scope -ne 'Local'){throw 'Unexpected drive'}
 $script:Drive=$Name;$script:Created=$true
}
function Remove-PSDrive {[CmdletBinding()]param($Name,$Scope,[switch]$Force) if($Name -ne $script:Drive -or $Scope -ne 'Local'){throw 'Wrong cleanup'};$script:Removed=$true}
function Get-Acl {
 [CmdletBinding()]param($Path,[switch]$Audit)
 if($script:FailRead){throw 'ACL read denied'}
 if(-not $Audit -or $Path -ne ($script:Drive+':\'+$script:ExpectedOU)){throw 'Wrong ACL path'}
 $script:ReadCount++;$after=$script:ReadCount -gt 1
 $acl=[pscustomobject]@{After=$after}
 $acl | Add-Member ScriptMethod GetSecurityDescriptorSddlForm {param($Section)
  if($Section -eq [System.Security.AccessControl.AccessControlSections]::Access){if($this.After -and $script:ChangeDacl){'DACL-CHANGED'}else{'DACL-ORIGINAL'}}
  else{if($script:AlreadyAudited -or $this.After){'SACL-AUDITED'}else{'SACL-ORIGINAL'}}
 }
 $acl | Add-Member ScriptMethod GetAuditRules {param($Explicit,$Inherited,$Type)
  if(-not $script:NoRule){[pscustomobject]@{IdentityReference=[pscustomobject]@{Value='S-1-1-0'};ObjectType=$script:SchemaId;AuditFlags=[System.Security.AccessControl.AuditFlags]::Success}}
 }
 $acl
}
function Get-ADRootDSE {[CmdletBinding()]param($Server) if($Server -ne 'DC02'){throw 'Wrong DC'};[pscustomobject]@{schemaNamingContext='CN=Schema,CN=Configuration,DC=lab,DC=test'}}
function Get-ADObject {[CmdletBinding()]param($Server,$SearchBase,$LDAPFilter,$Properties)
 if($Server -ne 'DC02' -or $LDAPFilter -ne '(lDAPDisplayName=ms-Mcs-AdmPwd)' -or $Properties -ne 'schemaIDGUID'){throw 'Wrong schema scope'}
 if($script:MissingGuid){[pscustomobject]@{}}else{[pscustomobject]@{schemaIDGUID=$script:SchemaId.ToByteArray()}}
}
function Set-AdmPwdAuditing {[CmdletBinding()]param($Identity,$AuditedPrincipals)
 if($Identity -ne $script:ExpectedOU -or $AuditedPrincipals -ne 'Everyone'){throw 'Wrong audit scope'}
 $script:AuditCalls++;if($script:FailAudit){throw 'Audit write denied'}
}
''' + setup + '\n$body={\n' + self.script + '\n}\n'
        harness += r'''
$failure=$null
try{$output=& $body -OrgUnit $script:ExpectedOU}catch{$failure=$_.Exception.Message}
[pscustomobject]@{failure=$failure;changed=$Ansible.Changed;created=$script:Created;removed=$script:Removed;calls=$script:AuditCalls;reads=$script:ReadCount;output=$output}|ConvertTo-Json -Compress -Depth 6
'''
        with tempfile.TemporaryDirectory(prefix='laps-audit-regression-') as directory:
            path = Path(directory) / 'check.ps1'
            path.write_text(harness)
            result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(path)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_new_audit_preserves_dacl_and_cleans_unique_drive(self):
        r=self.run_script();self.assertIsNone(r['failure']);self.assertTrue(r['changed']);self.assertTrue(r['removed']);self.assertEqual(r['calls'],1);self.assertTrue(r['output']['read_permissions_preserved'])

    def test_existing_audit_is_unchanged(self):
        r=self.run_script('$script:AlreadyAudited=$true');self.assertIsNone(r['failure']);self.assertFalse(r['changed']);self.assertTrue(r['removed'])

    def test_module_failure_does_not_claim_change(self):
        r=self.run_script('$script:FailModule=$true');self.assertIn('Module missing',r['failure']);self.assertFalse(r['changed']);self.assertFalse(r['created']);self.assertEqual(r['calls'],0)

    def test_drive_failure_does_not_remove_unowned_drive(self):
        r=self.run_script('$script:FailCreate=$true');self.assertIn('Drive creation failed',r['failure']);self.assertFalse(r['removed']);self.assertEqual(r['calls'],0)

    def test_acl_read_failure_cleans_owned_drive_before_any_write(self):
        r=self.run_script('$script:FailRead=$true');self.assertIn('read denied',r['failure']);self.assertTrue(r['removed']);self.assertEqual(r['calls'],0)

    def test_invalid_schema_prevents_write(self):
        r=self.run_script('$script:MissingGuid=$true');self.assertIn('schemaIDGUID',r['failure']);self.assertTrue(r['removed']);self.assertEqual(r['calls'],0)

    def test_audit_write_failure_cleans_drive(self):
        r=self.run_script('$script:FailAudit=$true');self.assertIn('Audit write denied',r['failure']);self.assertTrue(r['removed'])

    def test_unexpected_dacl_change_fails(self):
        r=self.run_script('$script:ChangeDacl=$true');self.assertIn('access permissions',r['failure']);self.assertTrue(r['removed'])

    def test_missing_expected_audit_rule_fails(self):
        r=self.run_script('$script:NoRule=$true');self.assertIn('not verified',r['failure']);self.assertTrue(r['removed'])


if __name__=='__main__':unittest.main()
