import unittest
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from goad.command.linux import LinuxCommand


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
        guard = template.split('Vagrant.configure', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            virsh = Path(temp, 'virsh')
            virsh.write_text('#!/bin/sh\nprintf "%s" "$TEST_POOL_XML"\n')
            virsh.chmod(0o755)
            env = dict(os.environ, PATH=temp + os.pathsep + os.environ['PATH'])
            for path in ['/wrong', '/mnt/SSD_DATA/Virtualization/KVM/GOAD']:
                env['TEST_POOL_XML'] = f'<pool><target><path>{path}</path></target></pool>'
                for command in ['up', 'reload', 'resume', 'status', 'halt', 'destroy']:
                    with self.subTest(path=path, command=command):
                        result = subprocess.run(
                            ['ruby', '-e', guard, '--', command],
                            env=env, capture_output=True, text=True,
                        )
                        blocked = path == '/wrong' and command in ['up', 'reload', 'resume']
                        self.assertEqual(result.returncode != 0, blocked, result.stderr)
