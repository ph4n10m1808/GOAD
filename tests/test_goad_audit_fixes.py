import ast
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from goad.command.linux import LinuxCommand
from goad.elastic_agent import ConfigurationError, configure, parse_options
from goad.goadpath import GoadPath
from goad.provider.vagrant import exchange_media
from goad.provider.vagrant.libvirt_images import BaseImages, InspectionError
from goad.provider.vagrant.storage import storage_mount_root, storage_pool_for_lab

ROOT = Path(__file__).resolve().parents[1]


class GoadAuditFixesTest(unittest.TestCase):
    def test_gd03_exchange_media_sha256_verification_for_download_and_cache(self):
        payload = b'A' * 16
        good_sha = hashlib.sha256(payload).hexdigest()
        bad_sha = '0' * 64

        class FakeResponse(io.BytesIO):
            headers = {'Content-Length': str(len(payload))}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            iso_path = Path(directory) / 'ExchangeServer2019-x64-CU9.ISO'
            with patch.dict(os.environ, {'GOAD_EXCHANGE_ISO_SHA256': 'not-hex'}):
                with self.assertRaises(RuntimeError):
                    exchange_media._prepare_locked(iso_path)

            with patch.dict(os.environ, {'GOAD_EXCHANGE_ISO_SHA256': bad_sha}), patch(
                'goad.provider.vagrant.exchange_media.urllib.request.urlopen',
                return_value=FakeResponse(payload),
            ):
                with self.assertRaisesRegex(RuntimeError, 'SHA-256 mismatch'):
                    exchange_media._prepare_locked(iso_path)
            self.assertFalse(iso_path.exists())

            iso_path.write_bytes(payload)
            fake_stat = SimpleNamespace(st_mode=iso_path.stat().st_mode, st_size=2 * 1024 * 1024 * 1024)
            with patch.dict(os.environ, {'GOAD_EXCHANGE_ISO_SHA256': good_sha}), patch.object(
                Path, 'stat', return_value=fake_stat
            ):
                self.assertEqual(exchange_media._prepare_locked(iso_path), iso_path)
            with patch.dict(os.environ, {'GOAD_EXCHANGE_ISO_SHA256': bad_sha}), patch.object(
                Path, 'stat', return_value=fake_stat
            ):
                with self.assertRaisesRegex(RuntimeError, 'Cached Exchange ISO SHA-256 mismatch'):
                    exchange_media._prepare_locked(iso_path)

        exchange_pre = (ROOT / 'extensions/exchange/ansible/roles/ludus_exchange/tasks/ludus-exchange-pre.yml').read_text()
        self.assertIn('exchange_dotnet_sha256', exchange_pre)
        self.assertIn('ucma_runtime_sha256', exchange_pre)

    def test_gd05_elastic_agent_token_file_and_env_handoff(self):
        options = parse_options('elastic_agent --ip 192.168.50.10 --token-file /tmp/token.txt')
        self.assertEqual(options['token_file'], '/tmp/token.txt')
        with self.assertRaises(ConfigurationError):
            parse_options('elastic_agent --token ABC --token-file /tmp/token.txt')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_file = root / 'fleet.token'
            token_file.write_text('TOKEN123==\n', encoding='utf-8')
            os.chmod(token_file, 0o644)
            with self.assertRaisesRegex(ConfigurationError, 'chmod 600'):
                configure(root, options={'ip': '192.168.50.10', 'token_file': str(token_file), 'crt': '/missing', 'version': '9.5.6'})

            os.chmod(token_file, 0o600)
            link_path = root / 'token.link'
            link_path.symlink_to(token_file)
            with self.assertRaisesRegex(ConfigurationError, 'non-symlink'):
                configure(root, options={'ip': '192.168.50.10', 'token_file': str(link_path), 'crt': '/missing', 'version': '9.5.6'})

        tasks = (ROOT / 'extensions/elastic_agent/ansible/roles/elastic_agent_windows/tasks/main.yml').read_text()
        self.assertNotIn('--enrollment-token={{', tasks)
        self.assertIn('FLEET_ENROLLMENT_TOKEN:', tasks)

    def test_gd08_storage_env_overrides_and_portable_docs(self):
        with patch.dict(os.environ, {
            'GOAD_STORAGE_POOL': 'CUSTOM_POOL',
            'GOAD_STORAGE_POOL_PATH': '/srv/kvm/goad',
            'GOAD_STORAGE_MOUNT_ROOT': '',
        }, clear=False):
            self.assertEqual(storage_pool_for_lab('GOAD-Light'), ('CUSTOM_POOL', Path('/srv/kvm/goad')))
            self.assertIsNone(storage_mount_root())

        libvirt_doc = (ROOT / 'docs/mkdocs/docs/providers/libvirt.md').read_text()
        self.assertNotIn('/mnt/SSD_DATA_1/Code/01-Active-Projects/DATN', libvirt_doc)
        self.assertNotIn('deploy/deploy/docs/', libvirt_doc)

    def test_gd09_docker_ansible_scopes_mounts_and_runs_non_root(self):
        project_root = os.path.realpath(GoadPath.get_project_path())
        fake_instance = os.path.join(project_root, 'workspace', 'demo-instance')
        with patch('goad.command.linux.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(LinuxCommand().run_docker_ansible(
                'site.yml', '.', '/goad/ansible', ['docker'], instance_path=fake_instance,
            ))
        argv = run.call_args.args[0]
        self.assertNotIn(f'{project_root}:/goad', argv)
        self.assertIn(f'{os.path.join(project_root, "ansible")}:/goad/ansible', argv)
        self.assertIn(f'{fake_instance}:/goad/workspace/demo-instance', argv)
        self.assertIn('--user', argv)
        self.assertIn(f'{os.getuid()}:{os.getgid()}', argv)

    def test_gd10_temporary_volume_and_inspection_pool_validation(self):
        source = ROOT / 'goad/provider/vagrant/libvirt.py'
        cls = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef))
        methods = [
            n for n in cls.body
            if isinstance(n, ast.FunctionDef)
            and n.name in ('_validated_cleanup_path', '_cleanup_temporary_volume')
        ]
        namespace = dict(Path=Path, ET=ET, os=os, Log=Mock(), InspectionError=InspectionError)
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)

        virsh = Mock()
        provider = SimpleNamespace(
            STORAGE_POOL_PATH=Path('/pool'),
            _virsh=virsh,
            _state_dir=lambda vm: Path('/tmp/nonexistent-state'),
        )
        provider._validated_cleanup_path = lambda value, base_image=False: namespace['_validated_cleanup_path'](
            provider, value, base_image=base_image
        )
        namespace['_cleanup_temporary_volume'](provider, 'DC01', '/etc/passwd')
        virsh.assert_not_called()

        images = BaseImages(lambda args: SimpleNamespace(returncode=1, stdout=''), allowed_inspection_roots=[Path('/pool')])
        with patch('goad.provider.vagrant.libvirt_images.subprocess.run', side_effect=PermissionError):
            with self.assertRaises(InspectionError):
                images.external_chain('/etc/shadow')
            with self.assertRaises(InspectionError):
                images.external_chain('/outside/disk.qcow2')

    def test_gd11_delete_instance_preserves_soc_credentials(self):
        source = ROOT / 'goad/instance.py'
        cls = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'LabInstance')
        methods = [
            n for n in cls.body
            if isinstance(n, ast.FunctionDef)
            and n.name in ('_preserve_soc_credentials', 'delete_instance')
        ]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            instance_dir = project / 'workspace' / 'inst-01'
            creds_dir = instance_dir / '.soc-credentials'
            creds_dir.mkdir(parents=True)
            cred_file = creds_dir / 'soc_siem.txt'
            cred_file.write_text('secret-value\n', encoding='utf-8')

            utils = SimpleNamespace(confirm=lambda prompt: True)
            goad_path = SimpleNamespace(get_project_path=lambda: str(project))
            namespace = dict(os=os, shutil=shutil, Log=Mock(), Utils=utils, GoadPath=goad_path)
            exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)

            instance = SimpleNamespace(
                instance_id='inst-01',
                instance_path=str(instance_dir),
                provider=SimpleNamespace(destroy=lambda: True),
            )
            instance._preserve_soc_credentials = lambda: namespace['_preserve_soc_credentials'](instance)
            self.assertTrue(namespace['delete_instance'](instance))

            self.assertFalse(instance_dir.exists())
            preserved_root = project / 'workspace' / '.preserved-credentials'
            preserved = list(preserved_root.glob('inst-01-*/.soc-credentials/soc_siem.txt'))
            self.assertEqual(len(preserved), 1)
            self.assertEqual(preserved[0].read_text(encoding='utf-8'), 'secret-value\n')
            self.assertEqual(os.stat(preserved[0]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(preserved_root).st_mode & 0o777, 0o700)

    def test_gd14_gd15_and_batch_b_source_contracts(self):
        self.assertFalse((ROOT / 'extensions/elastic_agent/ansible/roles/elastic_agent_windows/files/Initialize-SocAD-EntityReader.ps1').exists())

        gitignore = (ROOT / '.gitignore').read_text()
        self.assertNotIn('\ntests/\n', gitignore)

        sysmon_tasks = (ROOT / 'extensions/sysmon/ansible/roles/sysmon_windows/tasks/main.yml').read_text()
        self.assertIn('CN=Microsoft Corporation', sysmon_tasks)
        self.assertIn('sysmon_archive_checksum', sysmon_tasks)

        mssql_audit = (ROOT / 'ansible/roles/mssql/tasks/audit.yml').read_text()
        self.assertIn('mssql_audit_batch_completed', mssql_audit)

        laps_dc = (ROOT / 'ansible/roles/laps/dc/tasks/install.yml').read_text()
        self.assertIn('ansible.windows.win_get_url', laps_dc)
        self.assertIn('Get-AuthenticodeSignature', laps_dc)
        self.assertNotIn('Configure Expiration Protection on GPO', laps_dc)

        laps_srv = (ROOT / 'ansible/roles/laps/server/tasks/install.yml').read_text()
        self.assertIn('ansible.windows.win_get_url', laps_srv)
        self.assertIn('ProvisioningUser', laps_srv)
        self.assertNotIn('Get-LocalUser -Name vagrant -ErrorAction Stop', laps_srv)

        laps_perm = (ROOT / 'ansible/roles/laps/permissions/tasks/main.yml').read_text()
        self.assertNotIn('win_shell:', laps_perm)
        self.assertIn('AllowedPrincipal', laps_perm)

        dracarys_inv = (ROOT / 'ad/DRACARYS/providers/libvirt/inventory').read_text()
        self.assertIn('StrictHostKeyChecking=accept-new', dracarys_inv)
        self.assertNotIn('StrictHostKeyChecking=no', dracarys_inv)

        for lab_name in ('MINILAB', 'NHA', 'SCCM', 'TEMPLATE'):
            vf = (ROOT / f'ad/{lab_name}/providers/libvirt/Vagrantfile').read_text()
            self.assertIn(':box_version', vf)

        nft_conf = (ROOT / 'template/provider/libvirt/soc-bridge-nftables.conf').read_text()
        self.assertIn('virbr50', nft_conf)
        self.assertIn('table inet goad_soc_isolation', nft_conf)

        cert_audit = (ROOT / 'ansible/roles/settings/lab_auditing/files/Configure-LabCertificateObjectAuditing.ps1').read_text()
        self.assertIn('$TemplateNames', cert_audit)
        self.assertIn('$missingTemplates', cert_audit)

        gmsa_audit = (ROOT / 'ansible/roles/settings/lab_auditing/files/Configure-LabGmsaAuditing.ps1').read_text()
        self.assertIn('$MaxAttempts', gmsa_audit)

        ca_audit = (ROOT / 'ansible/roles/settings/lab_auditing/files/Configure-LabCaAuditing.ps1').read_text()
        self.assertIn('($audit -band 127) -ne 127', ca_audit)


if __name__ == '__main__':
    unittest.main()
