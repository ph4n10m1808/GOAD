"""Destructive Exchange operations must stay scoped to their own VM identity."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from goad.provider.vagrant.libvirt import LibvirtProvider


class ExchangeRemovalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.provider = LibvirtProvider.__new__(LibvirtProvider)
        self.provider.lab_name = 'GOAD-Light'
        self.provider.path = str(self.root / 'abc123-goad-light-libvirt/provider')
        self.provider.STORAGE_POOL_PATH = self.root / 'pool'
        self.provider.command = SimpleNamespace(run_vagrant=Mock(return_value=True))
        self.vm = 'GOAD-Light-SRV01'
        self.name = 'GOAD-Light-abc123' + self.vm
        self.identifier = '00000000-0000-4000-8000-000000000002'
        self.id_file = Path(self.provider.path) / '.vagrant/machines' / self.vm / 'libvirt/id'
        self.id_file.parent.mkdir(parents=True)
        self.id_file.write_text(self.identifier)
        self.base = self.root / 'base'
        self.base.mkdir()
        (self.base / 'Vagrantfile').write_text('GOAD-Light-SRV02')
        self.patch = patch('goad.provider.vagrant.libvirt.GoadPath.get_lab_provider_path', return_value=str(self.base))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.provider._virsh = Mock(side_effect=lambda args: SimpleNamespace(returncode=0, stdout='shut off' if args[0]=='domstate' else ''))
        self.provider._detach_external_virtio_iso = Mock(return_value=True)
        self.xml = f'<domain><name>{self.name}</name><uuid>{self.identifier}</uuid><devices><disk device="disk"><source file="{self.provider.STORAGE_POOL_PATH / (self.name + ".img")}"/></disk></devices></domain>'
        self.provider._dump_domain_xml = Mock(side_effect=lambda name: self.xml)

    def test_rejects_base_iis_identity_without_any_lifecycle(self):
        self.xml = self.xml.replace(self.name, self.name.replace('SRV01', 'SRV02'))
        self.assertFalse(self.provider.remove_extension('exchange'))
        self.provider.command.run_vagrant.assert_not_called()

    def test_rejects_foreign_disk_without_any_lifecycle(self):
        self.xml = self.xml.replace(str(self.provider.STORAGE_POOL_PATH), '/foreign-pool')
        self.assertFalse(self.provider.remove_extension('exchange'))
        self.provider.command.run_vagrant.assert_not_called()

    def test_failed_clean_stop_never_destroys(self):
        self.provider.command.run_vagrant.return_value = False
        self.assertFalse(self.provider.remove_extension('exchange'))
        self.assertEqual([call.args[0] for call in self.provider.command.run_vagrant.call_args_list], [['halt', self.vm]])

    def test_native_cancel_keeps_removal_unverified(self):
        self.assertFalse(self.provider.remove_extension('exchange'))
        self.assertTrue(self.id_file.exists())

    def test_success_targets_only_exchange_and_preserves_base_image_cleanup(self):
        def lifecycle(args, path):
            if args[0] == 'destroy':
                self.id_file.unlink()
            return True
        self.provider.command.run_vagrant.side_effect = lifecycle
        self.provider._destroy_with_image_cleanup = Mock(side_effect=AssertionError('Broad cleanup is forbidden'))
        self.assertTrue(self.provider.remove_extension('exchange'))
        self.assertEqual([call.args[0] for call in self.provider.command.run_vagrant.call_args_list], [['halt', self.vm], ['destroy', self.vm]])
        self.provider._destroy_with_image_cleanup.assert_not_called()

    def test_short_domain_can_keep_its_existing_instance_named_disk(self):
        self.xml = self.xml.replace('<name>' + self.name + '</name>', '<name>' + self.vm + '</name>')
        def lifecycle(args, path):
            if args[0] == 'destroy':
                self.id_file.unlink()
            return True
        self.provider.command.run_vagrant.side_effect = lifecycle
        self.assertTrue(self.provider.remove_extension('exchange'))

    def test_other_instance_named_disk_is_rejected_before_halt(self):
        self.xml = self.xml.replace('<name>' + self.name + '</name>', '<name>' + self.vm + '</name>')
        self.xml = self.xml.replace('/' + self.name + '.img', '/GOAD-Light-foreign' + self.vm + '.img')
        self.assertFalse(self.provider.remove_extension('exchange'))
        self.provider.command.run_vagrant.assert_not_called()

    def test_full_goad_and_other_extension_never_destroy(self):
        self.provider.lab_name = 'GOAD'
        self.assertFalse(self.provider.remove_extension('exchange'))
        self.provider.lab_name = 'GOAD-Light'
        self.assertFalse(self.provider.remove_extension('sysmon'))
        self.provider.command.run_vagrant.assert_not_called()
