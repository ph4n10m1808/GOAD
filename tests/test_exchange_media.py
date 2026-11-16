"""Media ownership survives a domain rename without weakening UUID checks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from goad.provider.vagrant import exchange_media


class ExchangeMediaIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / 'abc123-goad-light-libvirt/provider'
        self.vm = 'GOAD-Light-SRV01'
        self.uuid = '00000000-0000-4000-8000-000000000002'
        identifier = self.workspace / '.vagrant/machines' / self.vm / 'libvirt/id'
        identifier.parent.mkdir(parents=True)
        identifier.write_text(self.uuid)
        self.iso = Path('/cache/ExchangeServer2019-x64-CU9.ISO')

    def xml(self, name, identifier=None):
        return f'<domain><name>{name}</name><uuid>{identifier or self.uuid}</uuid><devices><disk type="file" device="cdrom"><source file="{self.iso}"/><target dev="hdd" bus="ide"/><readonly/></disk></devices></domain>'

    def run_attach(self, xml):
        with patch.object(exchange_media, 'virsh') as virsh, patch.object(exchange_media, 'media_path', return_value=self.iso), patch.object(exchange_media, 'prepare', return_value=self.iso) as prepare:
            virsh.side_effect = lambda *args: 'running' if args[0] == 'domstate' else xml
            exchange_media.change(self.workspace, 'attach', self.vm)
            self.assertTrue(all(c.args[0] in ['dumpxml', 'domstate'] for c in virsh.call_args_list))
            prepare.assert_called_once()

    def test_short_name_with_matching_uuid_needs_no_media_mutation(self):
        self.run_attach(self.xml(self.vm))

    def test_exact_legacy_instance_name_remains_supported(self):
        self.run_attach(self.xml('GOAD-Light-abc123' + self.vm))

    def test_foreign_instance_or_uuid_is_rejected_before_prepare(self):
        for name, identifier in [('GOAD-Light-foreign' + self.vm, self.uuid), (self.vm, '00000000-0000-4000-8000-000000000003'), ('GOAD-Light-SRV02', self.uuid)]:
            with self.subTest(name=name, identifier=identifier), patch.object(exchange_media, 'virsh') as virsh, patch.object(exchange_media, 'prepare') as prepare:
                virsh.side_effect = lambda *args: 'running' if args[0] == 'domstate' else self.xml(name, identifier)
                with self.assertRaisesRegex(RuntimeError, 'domain identity differs'):
                    exchange_media.change(self.workspace, 'attach', self.vm)
                prepare.assert_not_called()
                self.assertTrue(all(c.args[0] in ['dumpxml', 'domstate'] for c in virsh.call_args_list))


if __name__ == '__main__':
    unittest.main()
