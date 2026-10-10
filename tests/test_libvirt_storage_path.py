import unittest
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from goad.command.linux import LinuxCommand
from goad.provider.vagrant.libvirt import LibvirtProvider
from goad.provider.vagrant.storage import storage_pool_for_lab


class StoragePathTest(unittest.TestCase):
    def test_pool_target_must_match_required_directory(self):
        for path, expected in [
            ('/mnt/SSD_DATA/Virtualization/KVM/GOAD', True),
            ('/mnt/SSD_DATA/Virtualization/KVM', False),
        ]:
            with self.subTest(path=path), patch(
                'goad.command.linux.subprocess.run',
                return_value=SimpleNamespace(
                    returncode=0, stdout=f'<pool><target><path>{path}</path></target></pool>'
                ),
            ):
                self.assertEqual(LinuxCommand()._validate_libvirt_pool_path(), expected)

    def test_light_provider_uses_second_ssd_and_rejects_old_pool(self):
        provider = LibvirtProvider("GOAD-Light")
        self.assertEqual(provider.command.libvirt_pool_name, "GOAD-Light")
        self.assertEqual(provider.STORAGE_POOL_PATH,
                         Path("/mnt/SSD_DATA_1/Virtualization/KVM/GOAD-Light"))
        for target, valid in [
            (str(provider.STORAGE_POOL_PATH), True),
            ("/mnt/SSD_DATA/Virtualization/KVM/GOAD", False),
        ]:
            with self.subTest(target=target), patch(
                "goad.command.linux.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=
                    f"<pool><target><path>{target}</path></target></pool>"),
            ) as run, patch('goad.command.linux.os.path.ismount', return_value=True):
                self.assertEqual(provider.command._validate_libvirt_pool_path(), valid)
                self.assertEqual(run.call_args.args[0][-1], "GOAD-Light")
        self.assertEqual(LibvirtProvider("GOAD").command.libvirt_pool_name, "GOAD")

    def test_unmounted_second_ssd_blocks_light_before_libvirt(self):
        provider = LibvirtProvider("GOAD-Light")
        with patch('goad.command.linux.os.path.ismount', return_value=False), patch(
            'goad.command.linux.subprocess.run'
        ) as run:
            self.assertFalse(provider.command.refresh_libvirt_pool())
            run.assert_not_called()

    def test_wrong_pool_prevents_refresh(self):
        with patch('goad.command.linux.subprocess.run', return_value=SimpleNamespace(
            returncode=0, stdout='<pool><target><path>/wrong</path></target></pool>'
        )) as run:
            self.assertFalse(LinuxCommand().refresh_libvirt_pool())
            self.assertEqual(run.call_count, 1)

    def test_missing_virsh_is_reported_without_traceback(self):
        with patch('goad.command.linux.subprocess.run', side_effect=FileNotFoundError()):
            self.assertFalse(LinuxCommand()._validate_libvirt_pool_path())

    def test_invalid_or_unavailable_pool_is_rejected(self):
        for code, xml in [(1, ''), (0, '<broken'), (0, '<pool/>')]:
            with self.subTest(code=code, xml=xml), patch(
                'goad.command.linux.subprocess.run',
                return_value=SimpleNamespace(returncode=code, stdout=xml, stderr='pool unavailable'),
            ):
                self.assertFalse(LinuxCommand()._validate_libvirt_pool_path())

    def test_vagrant_guard_blocks_wrong_storage_but_allows_cleanup(self):
        template = (Path(__file__).resolve().parents[1] /
                    'template/provider/libvirt/Vagrantfile').read_text()
        guard_template = template.split('Vagrant.configure', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            virsh = Path(temp, 'virsh')
            virsh.write_text('#!/bin/sh\nprintf "%s" "$TEST_POOL_XML"\n')
            virsh.chmod(0o755)
            mountpoint = Path(temp, 'mountpoint')
            mountpoint.write_text('#!/bin/sh\nexit "$TEST_MOUNT_EXIT"\n')
            mountpoint.chmod(0o755)
            env = dict(os.environ, PATH=temp + os.pathsep + os.environ['PATH'])
            for lab_name in ['GOAD', 'GOAD-Light']:
                guard = guard_template.replace('{{lab_name}}', lab_name)
                required = str(storage_pool_for_lab(lab_name)[1])
                for mounted in [True, False]:
                    env['TEST_MOUNT_EXIT'] = '0' if mounted else '1'
                    for target in ['/wrong', required, str(storage_pool_for_lab('GOAD')[1])]:
                        env['TEST_POOL_XML'] = f'<pool><target><path>{target}</path></target></pool>'
                        for command in ['up', 'reload', 'resume', 'status', 'halt', 'destroy']:
                            with self.subTest(lab=lab_name, mounted=mounted, target=target, command=command):
                                result = subprocess.run(
                                    ['ruby', '-e', guard, '--', command],
                                    env=env, capture_output=True, text=True,
                                )
                                unsafe = target != required or (lab_name == 'GOAD-Light' and not mounted)
                                blocked = unsafe and command in ['up', 'reload', 'resume']
                                self.assertEqual(result.returncode != 0, blocked, result.stderr)
