"""Offline regression checks for IIS readback; no Windows/guest connection."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'ansible/roles/settings/legacy_ad_lab/files/Configure-LabCaRelay.ps1'
HARNESS = r'''
$Ansible = [pscustomobject]@{Changed=$false}
$writes = 0
function Import-Module { [CmdletBinding()]param($Name) }
function Get-Service { [CmdletBinding()]param($Name) [pscustomobject]@{Name='CertSvc'} }
function Get-WebApplication { param($Site,$Name) [pscustomobject]@{Name='certsrv'} }
function Get-WebBinding { param($Name,$Protocol) if ($State.http) { 'existing binding' } }
function Assert-Scope($PSPath,$Location) {
    if ($PSPath -ne 'MACHINE/WEBROOT/APPHOST' -or $Location -ne 'Default Web Site/certsrv') {
        throw 'Wrong IIS configuration scope'
    }
}
function Get-WebConfigurationProperty {
    param($PSPath,$Location,$Filter,$Name)
    Assert-Scope $PSPath $Location
    if ($Filter -like '*/anonymousAuthentication') { $values = @($State.anonymous) }
    elseif ($Filter -like '*/windowsAuthentication') { $values = @($State.auth) }
    elseif ($Filter -like '*/providers/add') { $values = @($State.providers) }
    elseif ($Filter -like '*/extendedProtection') { $values = @($State.epa) }
    elseif ($Filter -eq 'system.webServer/security/access') { $values = @($State.ssl) }
    else { throw 'Unexpected IIS read' }
    foreach ($value in $values) {
        if ($State.wrapped) { [pscustomobject]@{Value=$value} } else { $value }
    }
}
function Set-WebConfigurationProperty {
    param($PSPath,$Location,$Filter,$Name,$Value)
    Assert-Scope $PSPath $Location
    $script:writes++
    if ($State.ignore_writes) { return }
    if ($Filter -like '*/anonymousAuthentication') { $State.anonymous = $Value }
    elseif ($Filter -like '*/windowsAuthentication') { $State.auth = $Value }
    elseif ($Filter -like '*/extendedProtection') { $State.epa = 0 }
    elseif ($Filter -eq 'system.webServer/security/access') { $State.ssl = 0 }
    else { throw 'Unexpected IIS write' }
}
function Add-WebConfigurationProperty {
    param($PSPath,$Location,$Filter,$Name,$Value)
    Assert-Scope $PSPath $Location
    $script:writes++
    if (-not $State.ignore_writes) { $State.providers += $Value.value }
}
'''


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is needed for offline checks')
class LegacyCaRelayTest(unittest.TestCase):
    def run_source(self, **overrides):
        state = dict(anonymous=False, auth=True, providers=['Negotiate', 'NTLM'],
                     epa='None', ssl='None', http=True, wrapped=True,
                     ignore_writes=False)
        state.update(overrides)
        setup = "$State = ConvertFrom-Json @'\n" + json.dumps(state) + "\n'@\n"
        run = """
try {
    $receipt = . 'SOURCE'
    $result = @{ok=$true; changed=$Ansible.Changed; writes=$writes; receipt=$receipt}
} catch {
    $result = @{ok=$false; changed=$Ansible.Changed; writes=$writes; error=$_.Exception.Message}
}
$result | ConvertTo-Json -Depth 6 -Compress
""".replace('SOURCE', str(SOURCE).replace("'", "''"))
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command',
                                 HARNESS + setup + run], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_compliant_named_and_numeric_values_are_unchanged(self):
        for wrapped in (False, True):
            for value in ('None', 0, '0'):
                with self.subTest(wrapped=wrapped, value=value):
                    result = self.run_source(wrapped=wrapped, epa=value, ssl=value)
                    self.assertTrue(result['ok'], result)
                    self.assertFalse(result['changed'])
                    self.assertEqual(result['writes'], 0)

    def test_string_booleans_are_not_truthy_false_strings(self):
        result = self.run_source(anonymous='False', auth='True', wrapped=False)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['writes'], 0)

    def test_required_changes_recheck_numeric_readback(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                result = self.run_source(anonymous=True, auth=False, providers=[],
                                         epa=2, ssl=8, wrapped=wrapped)
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['changed'])
                self.assertEqual(result['writes'], 5)

    def test_missing_http_binding_fails_before_writes(self):
        result = self.run_source(http=False, anonymous=True)
        self.assertFalse(result['ok'])
        self.assertIn('no HTTP binding', result['error'])
        self.assertEqual(result['writes'], 0)

    def test_missing_or_invalid_scalar_cannot_be_accepted(self):
        for overrides in ({'epa': None}, {'ssl': None}, {'auth': 'invalid'}):
            with self.subTest(overrides=overrides):
                result = self.run_source(**overrides)
                self.assertFalse(result['ok'])
                self.assertEqual(result['writes'], 0)

    def test_failed_persistence_names_the_wrong_attribute(self):
        for overrides, expected in (({'epa': 2}, 'tokenChecking=2'),
                                    ({'ssl': 8}, 'sslFlags=8'),
                                    ({'providers': []}, 'NTLM provider is missing')):
            with self.subTest(overrides=overrides):
                result = self.run_source(ignore_writes=True, **overrides)
                self.assertFalse(result['ok'])
                self.assertIn(expected, result['error'])


if __name__ == '__main__':
    unittest.main()
