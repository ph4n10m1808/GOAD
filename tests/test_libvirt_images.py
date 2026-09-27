import ast
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from goad.provider.vagrant.libvirt_images import BaseImages, InspectionError

BASE = '/pool/windows_vagrant_box_image_2017.12.14_box.img'


class BaseImagesTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = {
            ('list', '--all', '--uuid'): '',
            ('pool-list', '--inactive', '--name'): '',
            ('pool-list', '--name'): 'GOAD\n',
            ('pool-dumpxml', 'GOAD'): '<pool type="dir"/>',
            ('pool-refresh', 'GOAD'): '',
            ('vol-list', 'GOAD'): ' Name  Path\n---------------\n base  ' + BASE + '\n',
            ('vol-dumpxml', 'base', '--pool', 'GOAD'): '<volume><target><path>' + BASE + '</path></target></volume>',
            ('vol-delete', BASE): '',
        }
        self.images = BaseImages(self.virsh)

    def virsh(self, args):
        self.calls.append(tuple(args))
        value = self.responses.get(tuple(args))
        return SimpleNamespace(returncode=0 if value is not None else 1, stdout=value or '')

    def test_deletes_only_candidate_base(self):
        self.assertEqual(self.images.cleanup({BASE, '/pool/unrelated.img'}), [BASE])
        self.assertEqual([c for c in self.calls if c[0] == 'vol-delete'], [('vol-delete', BASE)])

    def test_remaining_domain_or_inactive_pool_blocks_cleanup(self):
        for command in [('list', '--all', '--uuid'), ('pool-list', '--inactive', '--name')]:
            with self.subTest(command=command):
                self.responses[command] = 'remaining\n'
                with self.assertRaises(InspectionError):
                    self.images.cleanup({BASE})
                self.assertNotIn(('vol-delete', BASE), self.calls)
                self.responses[command] = ''

    def test_orphan_snapshot_volume_keeps_base(self):
        self.responses[('vol-list', 'GOAD')] += ' snapshot  /pool/snapshot.qcow2\n'
        self.responses[('vol-dumpxml', 'snapshot', '--pool', 'GOAD')] = '<volume><target><path>/pool/snapshot.qcow2</path></target><backingStore><path>' + BASE + '</path></backingStore></volume>'
        self.assertEqual(self.images.cleanup({BASE}), [])
        self.assertNotIn(('vol-delete', BASE), self.calls)

    def test_failed_refresh_or_invalid_xml_never_deletes(self):
        for command, value in [(('pool-refresh', 'GOAD'), None), (('vol-dumpxml', 'base', '--pool', 'GOAD'), 'broken')]:
            with self.subTest(command=command):
                original = self.responses[command]
                self.responses[command] = value
                with self.assertRaises(InspectionError):
                    self.images.cleanup({BASE})
                self.assertNotIn(('vol-delete', BASE), self.calls)
                self.responses[command] = original

    def test_candidate_comes_from_guest_backing_volume(self):
        self.responses[('vol-dumpxml', '/pool/guest.img')] = '<volume><backingStore><path>' + BASE + '</path></backingStore></volume>'
        xml = '<domain><devices><disk device="disk"><source file="/pool/guest.img"/></disk><disk device="cdrom"><source file="/virtio.iso"/></disk></devices></domain>'
        self.assertEqual(self.images.candidates(xml), {BASE})
        self.assertNotIn(('vol-dumpxml', '/virtio.iso'), self.calls)

    def test_no_candidates_does_nothing(self):
        self.assertEqual(self.images.cleanup(set()), [])
        self.assertEqual(self.calls, [])

    def test_running_independent_guest_allows_base_cleanup(self):
        self.responses[('list', '--all', '--uuid')] = 'guest'
        self.responses[('list', '--all', '--with-managed-save', '--uuid')] = ''
        self.responses[('snapshot-list', 'guest', '--name')] = ''
        xml = '<domain><devices><disk device="disk"><source file="/pool/guest.img"/></disk></devices></domain>'
        self.responses[('dumpxml', 'guest')] = xml
        self.responses[('dumpxml', 'guest', '--inactive')] = xml
        self.responses[('vol-list', 'GOAD')] += ' guest  /pool/guest.img\n'
        self.responses[('vol-dumpxml', 'guest', '--pool', 'GOAD')] = '<volume><target><path>/pool/guest.img</path></target></volume>'
        self.assertEqual(self.images.cleanup({BASE}, inspect_domains=True), [BASE])

    def test_flatten_waits_and_verifies_live_and_persistent_disks(self):
        linked = '<domain><devices><disk device="disk"><driver type="qcow2"/><source file="/pool/guest.img"/><target dev="vda"/><backingStore><source file="' + BASE + '"/></backingStore></disk></devices></domain>'
        independent = linked.replace('<backingStore><source file="' + BASE + '"/></backingStore>', '<backingStore/>')
        self.responses[('snapshot-list', 'guest', '--name')] = ''
        self.responses[('dumpxml', 'guest')] = linked
        self.responses[('dumpxml', 'guest', '--inactive')] = independent
        self.responses[('vol-dumpxml', '/pool/guest.img')] = '<volume><backingStore><path>' + BASE + '</path></backingStore></volume>'
        def virsh(args):
            if args[0] == 'blockpull':
                self.responses[('dumpxml', 'guest')] = independent
                self.responses[('vol-dumpxml', '/pool/guest.img')] = '<volume/>'
                self.responses[tuple(args)] = ''
            return self.virsh(args)
        self.images.virsh = virsh
        self.assertEqual(self.images.flatten('guest'), {BASE})
        self.assertIn(('blockpull', 'guest', 'vda', '--wait', '--verbose'), self.calls)
        self.assertIn(('dumpxml', 'guest', '--inactive'), self.calls)

    def test_flatten_uses_volume_backing_when_domain_xml_omits_it(self):
        xml = '<domain><devices><disk device="disk"><driver type="qcow2"/><source file="/pool/guest.img"/><target dev="vda"/></disk></devices></domain>'
        self.responses[('snapshot-list', 'guest', '--name')] = ''
        self.responses[('dumpxml', 'guest')] = xml
        self.responses[('dumpxml', 'guest', '--inactive')] = xml
        self.responses[('vol-dumpxml', '/pool/guest.img')] = '<volume><backingStore><path>' + BASE + '</path></backingStore></volume>'
        def virsh(args):
            if args[0] == 'blockpull':
                self.responses[('vol-dumpxml', '/pool/guest.img')] = '<volume/>'
                self.responses[tuple(args)] = ''
            return self.virsh(args)
        self.images.virsh = virsh
        self.assertEqual(self.images.flatten('guest'), {BASE})
        self.assertIn(('blockpull', 'guest', 'vda', '--wait', '--verbose'), self.calls)

    def test_flatten_rejects_backing_left_in_volume_after_successful_command(self):
        xml = '<domain><devices><disk device="disk"><driver type="qcow2"/><source file="/pool/guest.img"/><target dev="vda"/></disk></devices></domain>'
        self.responses[('snapshot-list', 'guest', '--name')] = ''
        self.responses[('dumpxml', 'guest')] = xml
        self.responses[('dumpxml', 'guest', '--inactive')] = xml
        self.responses[('vol-dumpxml', '/pool/guest.img')] = '<volume><backingStore><path>' + BASE + '</path></backingStore></volume>'
        self.responses[('blockpull', 'guest', 'vda', '--wait', '--verbose')] = ''
        with self.assertRaisesRegex(InspectionError, 'still has a backing file'):
            self.images.flatten('guest')
        self.assertFalse(any(c[0] == 'vol-delete' for c in self.calls))

    def test_snapshot_blocks_flatten_without_changing_disk(self):
        self.responses[('snapshot-list', 'guest', '--name')] = 'snapshot'
        with self.assertRaises(InspectionError):
            self.images.flatten('guest')
        self.assertFalse(any(c[0] == 'blockpull' for c in self.calls))

    def test_owned_auxiliary_disks_are_captured_but_shared_disks_are_not(self):
        self.responses[('vol-dumpxml', '/pool/lab-DC01.img')] = '<volume/>'
        self.responses[('vol-dumpxml', '/pool/lab-DC01-vdb.qcow2')] = '<volume/>'
        xml = '<domain><name>lab-DC01</name><devices>' + ''.join(
            '<disk device="disk"><source file="' + path + '"/></disk>'
            for path in ['/pool/lab-DC01.img', '/pool/lab-DC01-vdb.qcow2',
                         '/pool/shared.qcow2', '/pool/lab-DC01-other.qcow2']
        ) + '</devices></domain>'
        self.assertEqual(self.images.guest_volumes(xml),
                         {'/pool/lab-DC01.img', '/pool/lab-DC01-vdb.qcow2'})

    def test_external_disk_chain_is_checked_before_deletion(self):
        self.responses[('list', '--all', '--uuid')] = 'guest'
        self.responses[('list', '--all', '--with-managed-save', '--uuid')] = ''
        self.responses[('snapshot-list', 'guest', '--name')] = ''
        xml = '<domain><devices><disk device="disk"><source file="/external/disk.qcow2"/></disk></devices></domain>'
        self.responses[('dumpxml', 'guest')] = xml
        self.responses[('dumpxml', 'guest', '--inactive')] = xml
        for chain, expected in [([{'filename': '/external/disk.qcow2'}, {'filename': BASE}], []),
                                ([{'filename': '/external/disk.qcow2'}], [BASE])]:
            with patch('goad.provider.vagrant.libvirt_images.subprocess.run',
                       return_value=SimpleNamespace(stdout=json.dumps(chain))):
                self.assertEqual(self.images.cleanup({BASE}, inspect_domains=True), expected)
        self.calls.clear()
        with patch('goad.provider.vagrant.libvirt_images.subprocess.run', side_effect=PermissionError):
            with self.assertRaises(InspectionError):
                self.images.cleanup({BASE}, inspect_domains=True)
        self.assertFalse(any(c[0] == 'vol-delete' for c in self.calls))

    def test_root_owned_external_disk_uses_temporary_pool_and_checks_chain(self):
        external = '/external/win10.qcow2'
        calls = []
        def virsh(args):
            calls.append(args)
            if args[0] == 'pool-create':
                root = ET.parse(args[1]).getroot()
                self.assertEqual(root.findtext('./target/path'), '/external')
                self.assertEqual(root.get('type'), 'dir')
                return SimpleNamespace(returncode=0, stdout='')
            if args[0] == 'pool-destroy':
                return SimpleNamespace(returncode=0, stdout='')
            if args == ['vol-dumpxml', external]:
                return SimpleNamespace(returncode=1, stdout='')
            if args[0] == 'vol-dumpxml':
                path = external if '--pool' in args else BASE
                backing = '<backingStore><path>' + BASE + '</path></backingStore>' if path == external else ''
                return SimpleNamespace(returncode=0, stdout='<volume><target><path>' + path
                                       + '</path><format type="qcow2"/></target>' + backing + '</volume>')
            self.fail(args)
        self.images.virsh = virsh
        with patch('goad.provider.vagrant.libvirt_images.subprocess.run', side_effect=PermissionError):
            self.assertEqual(self.images.external_chain(external), {external, BASE})
        self.assertEqual(sum(c[0] == 'pool-create' for c in calls), 1)
        self.assertEqual(sum(c[0] == 'pool-destroy' for c in calls), 1)
        self.assertFalse(any(c[0] in ('pool-build', 'pool-delete', 'vol-delete') for c in calls))

    def test_temporary_inspection_pool_is_removed_when_volume_inspection_fails(self):
        calls = []
        def virsh(args):
            calls.append(args)
            return SimpleNamespace(returncode=0 if args[0].startswith('pool-') else 1, stdout='')
        self.images.virsh = virsh
        with patch('goad.provider.vagrant.libvirt_images.subprocess.run', side_effect=PermissionError):
            with self.assertRaises(InspectionError):
                self.images.external_chain('/external/win10.qcow2')
        self.assertEqual(calls[-1][0], 'pool-destroy')
        self.assertFalse(any(c[0] == 'vol-delete' for c in calls))

    def test_nested_backing_chain_protects_base(self):
        self.responses[('vol-list', 'GOAD')] += ' snapshot  /pool/snapshot.qcow2\n'
        self.responses[('vol-dumpxml', 'snapshot', '--pool', 'GOAD')] = (
            '<volume><target><path>/pool/snapshot.qcow2</path></target>'
            '<backingStore><path>/pool/middle.qcow2</path><backingStore><path>'
            + BASE + '</path></backingStore></backingStore></volume>')
        self.assertEqual(self.images.cleanup({BASE}), [])

    def test_provider_persists_bases_before_flatten_and_skips_cleanup_on_failure(self):
        source = Path(__file__).resolve().parents[1] / 'goad/provider/vagrant/libvirt.py'
        cls = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef))
        method_names = ('_state_owner', '_validated_cleanup_path', '_read_cleanup_state',
                        '_write_cleanup_state', '_saved_base_images', '_make_disks_independent')
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                   and n.name in method_names]
        for success in (True, False):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as path:
                images = Mock()
                images.candidates.return_value = {BASE}
                images.cleanup.return_value = []
                def flatten(domain):
                    payload = json.loads((Path(path) / '.goad/base-images.json').read_text())
                    self.assertEqual(payload['candidates'], [BASE])
                    if not success:
                        raise InspectionError('conversion failed')
                images.flatten.side_effect = flatten
                namespace = dict(Path=Path, json=json, ET=ET, os=os, Log=Mock(),
                                 BaseImages=Mock(return_value=images), InspectionError=InspectionError)
                exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
                provider = SimpleNamespace(path=path, _virsh=Mock(), _domain_uuid=lambda name: 'uuid')
                provider.STORAGE_POOL_PATH = Path('/pool')
                for method_name in method_names:
                    setattr(provider, method_name,
                            lambda *args, _name=method_name, **kwargs:
                            namespace[_name](provider, *args, **kwargs))
                self.assertEqual(namespace['_make_disks_independent'](provider, ['DC01']), success)
                self.assertEqual(images.cleanup.called, success)
                self.assertEqual(provider._saved_base_images(), {BASE})


if __name__ == '__main__':
    unittest.main()
