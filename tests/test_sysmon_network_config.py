"""Execute the actual Sysmon config preparation script without any guest/service."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'extensions/sysmon/ansible/roles/sysmon_windows/files/Prepare-SysmonConfig.ps1'
SOURCE = '''<!-- vendor attribution retained -->
<Sysmon schemaversion="4.50"><HashAlgorithms>sha256</HashAlgorithms><EventFiltering>
<RuleGroup name="keep" groupRelation="or"><ProcessCreate onmatch="exclude"><Image condition="is">keep.exe</Image></ProcessCreate></RuleGroup>
<RuleGroup name="" groupRelation="or"><NetworkConnect onmatch="include"><Image condition="is">cmd.exe</Image></NetworkConnect></RuleGroup>
<RuleGroup name="" groupRelation="or"><NetworkConnect onmatch="exclude"><DestinationIp condition="is">127.0.0.1</DestinationIp></NetworkConnect></RuleGroup>
<RuleGroup name="unchanged" groupRelation="or"><DnsQuery onmatch="exclude" /></RuleGroup>
</EventFiltering></Sysmon>'''


@unittest.skipUnless(shutil.which('pwsh'), 'Native PowerShell is required')
class SysmonNetworkConfigTest(unittest.TestCase):
    def run_script(self, source=SOURCE, mode='true', bad_hash=False, repeats=1, same_path=False):
        with tempfile.TemporaryDirectory(prefix='sysmon-network-check-') as directory:
            directory = Path(directory)
            origin, destination = directory/'upstream.xml', directory/'managed.xml'
            origin.write_text(source)
            if same_path:
                destination = origin
            expected = '0'*64 if bad_hash else hashlib.sha256(origin.read_bytes()).hexdigest()
            # Paths are generated temporary files; single quotes are escaped for PowerShell.
            quote = lambda path: "'" + str(path).replace("'", "''") + "'"
            wrapper = directory/'check.ps1'
            wrapper.write_text('''$Ansible=@{Changed=$false;Result=@{}}
$states=@();$failure=$null
try {
''' + f'''for($i=0;$i -lt {repeats};$i++) {{
    & {quote(SCRIPT)} -SourcePath {quote(origin)} -DestinationPath {quote(destination)} -ExpectedSha256 '{expected}' -NetworkConnectAll ${mode}
    $states+=@{{changed=$Ansible.Changed;result=$Ansible.Result}}
}}
''' + '''} catch {$failure=$_.Exception.Message}
[pscustomobject]@{failure=$failure;states=$states} | ConvertTo-Json -Depth 6 -Compress
''')
            result = subprocess.run(['pwsh','-NoProfile','-NonInteractive','-File',str(wrapper)],check=True,capture_output=True,text=True)
            data = json.loads(result.stdout)
            data['source'] = origin.read_text()
            data['output'] = destination.read_text() if destination.exists() else None
            return data

    def test_network_collect_all_preserves_every_other_rule(self):
        result = self.run_script()
        self.assertIsNone(result['failure'])
        self.assertEqual(result['source'], SOURCE)
        self.assertIn('<NetworkConnect onmatch="exclude" />', result['output'])
        self.assertEqual(result['output'].count('<NetworkConnect'), 1)
        self.assertIn('<!-- vendor attribution retained -->', result['output'])
        self.assertIn('<RuleGroup name="keep" groupRelation="or"><ProcessCreate onmatch="exclude"><Image condition="is">keep.exe</Image></ProcessCreate></RuleGroup>',result['output'])
        self.assertIn('<RuleGroup name="unchanged" groupRelation="or"><DnsQuery onmatch="exclude" /></RuleGroup>',result['output'])

    def test_repeated_generation_is_noop(self):
        result = self.run_script(repeats=2)
        self.assertIsNone(result['failure'])
        self.assertEqual([r['changed'] for r in result['states']], [True, False])

    def test_opt_out_preserves_pinned_upstream_policy(self):
        result = self.run_script(mode='false')
        self.assertIsNone(result['failure'])
        self.assertEqual(result['output'], SOURCE)

    def test_checksum_mismatch_refuses_output(self):
        result = self.run_script(bad_hash=True)
        self.assertIn('checksum differs', result['failure'])
        self.assertIsNone(result['output'])

    def test_shared_source_output_path_is_refused(self):
        result = self.run_script(same_path=True)
        self.assertIn('separate', result['failure'])
        self.assertEqual(result['source'], SOURCE)

    def test_changed_network_layout_is_refused(self):
        result = self.run_script(source=SOURCE.replace('onmatch="include"','onmatch="different"'))
        self.assertIn('layout differs', result['failure'])
        self.assertIsNone(result['output'])

    def test_unexpected_third_network_rule_is_refused(self):
        result = self.run_script(source=SOURCE.replace('</EventFiltering>','<NetworkConnect onmatch="exclude" /></EventFiltering>'))
        self.assertIn('layout differs', result['failure'])
        self.assertIsNone(result['output'])


if __name__ == '__main__':
    unittest.main()
