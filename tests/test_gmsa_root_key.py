"""Regression checks against the production script using native PowerShell lists."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
class KdsRootKeyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "ansible/roles/gmsa/tasks/main.yml"
        tasks = yaml.safe_load(path.read_text())
        cls.script = next(task["ansible.windows.win_powershell"]["script"]
                          for task in tasks if task["name"].startswith("Prepare one forest KDS"))

    def run_script(self, initial_keys, repeats=1):
        harness = r"""
$script:Keys = [System.Collections.Generic.List[object]]::new()
$script:AddCalls = 0
function Import-Module { [CmdletBinding()] param([string]$Name) }
function Get-KdsRootKey { Write-Output -NoEnumerate $script:Keys }
function Add-KdsRootKey {
    [CmdletBinding()] param([datetime]$EffectiveTime)
    $script:AddCalls++
    $id = [guid]::NewGuid()
    $script:Keys.Add([pscustomobject]@{KeyId=$id;EffectiveTime=$EffectiveTime})
    $id
}
$Ansible = @{Changed=$false}
""" + initial_keys + "\n$body = {\n" + self.script + "\n}\n"
        harness += f"$repeats = {repeats}\n" + r"""
$changed = @()
$failure = $null
try {
    for ($i=0; $i -lt $repeats; $i++) {
        $result = & $body
        $changed += $Ansible.Changed
    }
} catch { $failure = $_.Exception.Message }
[pscustomobject]@{adds=$script:AddCalls;count=$script:Keys.Count;changed=$changed;
    ids=@($result.key_ids);failure=$failure} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory(prefix="gmsa-key-regression-") as directory:
            path = Path(directory) / "check.ps1"
            path.write_text(harness)
            result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(path)],
                                    capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_empty_native_list_creates_once_and_rerun_reuses_key(self):
        result = self.run_script("", repeats=2)
        self.assertIsNone(result["failure"])
        self.assertEqual((result["adds"], result["count"]), (1, 1))
        self.assertEqual(result["changed"], [True, False])
        self.assertEqual(len(result["ids"]), 1)

    def test_existing_effective_keys_are_retained(self):
        result = self.run_script("""
$script:Keys.Add([pscustomobject]@{KeyId=[guid]::NewGuid();EffectiveTime=(Get-Date).AddHours(-12)})
$script:Keys.Add([pscustomobject]@{KeyId=[guid]::NewGuid();EffectiveTime=(Get-Date).AddHours(-11)})
""")
        self.assertIsNone(result["failure"])
        self.assertEqual((result["adds"], result["count"]), (0, 2))
        self.assertEqual(len(result["ids"]), 2)

    def test_future_key_is_not_replaced(self):
        result = self.run_script("""
$script:Keys.Add([pscustomobject]@{KeyId=[guid]::NewGuid();EffectiveTime=(Get-Date).AddHours(12)})
""")
        self.assertIn("not effective", result["failure"])
        self.assertEqual((result["adds"], result["count"]), (0, 1))

    def test_missing_key_id_fails_without_replacement(self):
        result = self.run_script("""
$script:Keys.Add([pscustomobject]@{EffectiveTime=(Get-Date).AddHours(-12)})
""")
        self.assertIn("metadata is invalid", result["failure"])
        self.assertEqual((result["adds"], result["count"]), (0, 1))

    def test_missing_effective_time_fails_without_replacement(self):
        result = self.run_script("""
$script:Keys.Add([pscustomobject]@{KeyId=[guid]::NewGuid()})
""")
        self.assertIn("metadata is invalid", result["failure"])
        self.assertEqual((result["adds"], result["count"]), (0, 1))


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
class KdsVisibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "ansible/roles/gmsa/tasks/main.yml"
        tasks = yaml.safe_load(path.read_text())
        task = next(task for task in tasks if task["name"].startswith("Wait for the selected KDS"))
        cls.script = task["ansible.windows.win_powershell"]["script"]
        cls.until = task["until"]
        cls.context = task["vars"]

    def run_script(self, rows, key_ids="@('11111111-2222-3333-4444-555555555555')"):
        harness = r"""
$env:COMPUTERNAME='DC02'
$Ansible=@{Changed=$false;Result=@{}}
$script:Queries=0
function Import-Module { [CmdletBinding()] param([string]$Name) }
function Get-ADRootDSE { param($Server) [pscustomobject]@{configurationNamingContext='CN=Configuration,DC=example,DC=test'} }
function Get-ADObject {
    param($Server,$SearchBase,$SearchScope,$LDAPFilter,$Properties)
    $script:Queries++
    if ($Server -ne 'DC02' -or $LDAPFilter -ne '(objectClass=msKds-ProvRootKey)' -or $Properties -ne 'cn') { throw 'Unexpected directory scope' }
""" + rows + "\n}\n$body = {\n" + self.script + "\n}\n"
        harness += "$failure=$null\ntry { $output=& $body -KeyIds " + key_ids + " } catch { $failure=$_.Exception.Message }\n"
        harness += r"""
[pscustomobject]@{failure=$failure;result=$Ansible.Result;output=$output;queries=$script:Queries;changed=$Ansible.Changed} | ConvertTo-Json -Compress -Depth 6
"""
        with tempfile.TemporaryDirectory(prefix="gmsa-visibility-regression-") as directory:
            path = Path(directory) / "check.ps1"
            path.write_text(harness)
            result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(path)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_single_explicit_cn_without_name(self):
        result = self.run_script("[pscustomobject]@{cn='{11111111-2222-3333-4444-555555555555}'}")
        self.assertIsNone(result["failure"])
        self.assertTrue(result["output"]["kds_key_visible"])
        self.assertFalse(result["changed"])

    def test_native_list_is_enumerated(self):
        result = self.run_script("""
$list=[System.Collections.Generic.List[object]]::new()
$list.Add([pscustomobject]@{cn='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'})
$list.Add([pscustomobject]@{cn='11111111-2222-3333-4444-555555555555'})
Write-Output -NoEnumerate $list
""")
        self.assertIsNone(result["failure"])
        self.assertTrue(result["output"]["kds_key_visible"])

    def test_empty_native_list_waits_for_replication(self):
        result = self.run_script("Write-Output -NoEnumerate ([System.Collections.Generic.List[object]]::new())")
        self.assertIn("has replicated", result["failure"])
        self.assertTrue(result["result"]["retryable"])
        self.assertEqual(result["result"]["visible_keys"], 0)

    def test_null_result_waits_without_null_method_call(self):
        result = self.run_script("Write-Output -NoEnumerate $null")
        self.assertIn("has replicated", result["failure"])
        self.assertTrue(result["result"]["retryable"])

    def test_name_is_a_valid_fallback(self):
        result = self.run_script("[pscustomobject]@{Name='11111111-2222-3333-4444-555555555555'}")
        self.assertIsNone(result["failure"])

    def test_object_guid_is_not_a_key_id(self):
        result = self.run_script("[pscustomobject]@{cn='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';ObjectGUID='11111111-2222-3333-4444-555555555555'}")
        self.assertTrue(result["result"]["retryable"])
        self.assertIn("has replicated", result["failure"])

    def test_missing_cn_and_name_stop_without_retries(self):
        result = self.run_script("[pscustomobject]@{ObjectClass='msKds-ProvRootKey'}")
        self.assertIn("no valid CN", result["failure"])
        self.assertFalse(result["result"]["retryable"])
        self.assertIn("retryable", self.until)

    def test_missing_selected_id_stops_before_query(self):
        result = self.run_script("throw 'Must not query'", "@()")
        self.assertIn("No effective KDS key", result["failure"])
        self.assertFalse(result["result"]["retryable"])
        self.assertEqual(result["queries"], 0)

    def test_invalid_selected_id_stops_before_query(self):
        result = self.run_script("throw 'Must not query'", "@('not-a-guid')")
        self.assertIn("identifier is missing or invalid", result["failure"])
        self.assertFalse(result["result"]["retryable"])
        self.assertEqual(result["queries"], 0)

    def test_directory_error_is_not_a_replication_retry(self):
        result = self.run_script("throw 'Directory unavailable'")
        self.assertEqual(result["failure"], "Directory unavailable")
        self.assertFalse(result["result"]["retryable"])

    def test_local_dc_computer_context_needs_no_forest_admin_credential(self):
        self.assertEqual(self.context["ansible_become_user"], "SYSTEM")
        self.assertNotIn("ansible_become_password", self.context)


if __name__ == "__main__":
    unittest.main()
