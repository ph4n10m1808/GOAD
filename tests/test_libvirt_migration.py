import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "goad_storage_migration",
    Path(__file__).resolve().parents[1] / "scripts/migrate_goad_light_storage.py",
)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class MigrationListingTest(unittest.TestCase):
    def test_quiet_listing_preserves_names_with_spaces(self):
        output = " disk with spaces.img   /pool/disk with spaces.img\n plain.img   /pool/plain.img\n"
        with patch.object(migration, "virsh", return_value=output) as call:
            self.assertEqual(migration.volume_names("GOAD-Light"),
                             ["disk with spaces.img", "plain.img"])
            call.assert_called_once_with("vol-list", "GOAD-Light")

    def test_ambiguous_listing_fails_closed(self):
        for output in [" Name  Path\n", " odd-volume\n", " name  relative-path\n"]:
            with self.subTest(output=output), patch.object(migration, "virsh", return_value=output):
                with self.assertRaises(RuntimeError):
                    migration.volume_names("GOAD-Light")


class MigrationDependencyGuardTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {'uuid': 'first', 'source': Path('/pool/first.img'), 'target': Path('/new/first.img')},
            {'uuid': 'second', 'source': Path('/pool/second.img'), 'target': Path('/new/second.img')},
        ]
        self.sources = {str(entry['source']) for entry in self.entries}

    def cleanup(self):
        migration.cleanup_sources(self.entries, 'GOAD', 'GOAD-Light', None, [])

    def test_batch_dependency_preflight_preserves_all_sources(self):
        # BaseImages also checks unmanaged backing chains, aliases, snapshots
        # and saved state. A referenced/unregistered source cannot be deleted.
        with patch.object(migration.BaseImages, 'unused', return_value=set()) as unused, patch.object(migration, 'virsh') as virsh:
            with self.assertRaises(RuntimeError):
                self.cleanup()
        unused.assert_called_once_with(self.sources, inspect_domains=True)
        virsh.assert_not_called()

    def test_unmanaged_guest_backing_chain_blocks_migration_cleanup(self):
        self.entries = [self.entries[0]]
        source = str(self.entries[0]['source'])

        def inspect(args):
            if args == ['list', '--all', '--uuid']:
                output = 'dependent-guest\n'
            elif args[0] == 'dumpxml':
                output = '<domain><devices><disk device="disk"><source file="/outside/guest.qcow2"/></disk></devices></domain>'
            elif args == ['pool-list', '--name']:
                output = 'GOAD\n'
            elif args[0] == 'pool-dumpxml':
                output = '<pool type="dir"/>'
            elif args[0] == 'vol-list':
                output = f' Name       Path\n------------------------\n first.img  {source}\n'
            elif args[0] == 'vol-dumpxml':
                output = f'<volume><target><path>{source}</path></target></volume>'
            elif args[0] in ('snapshot-list', 'pool-refresh') or args in (
                ['list', '--all', '--with-managed-save', '--uuid'],
                ['pool-list', '--inactive', '--name'],
            ):
                output = ''
            else:
                self.fail(f'Unexpected inspection command: {args}')
            return SimpleNamespace(returncode=0, stdout=output)

        with patch.object(migration, 'dependency_virsh', side_effect=inspect), patch.object(
            migration.BaseImages, 'external_chain', return_value={'/outside/guest.qcow2', source}
        ) as external_chain, patch.object(migration, 'virsh') as virsh:
            with self.assertRaises(RuntimeError):
                self.cleanup()
        external_chain.assert_called_once_with('/outside/guest.qcow2')
        virsh.assert_not_called()

    def test_inspection_failure_never_reaches_deletion(self):
        with patch.object(migration.BaseImages, 'unused', side_effect=RuntimeError('Cannot inspect backing chain')), patch.object(migration, 'virsh') as virsh:
            with self.assertRaises(RuntimeError):
                self.cleanup()
        virsh.assert_not_called()

    def test_source_identity_is_rechecked_before_deletion(self):
        with patch.object(migration.BaseImages, 'unused', return_value=self.sources), patch.object(migration, 'stopped'), patch.object(
            migration, 'virsh', return_value='<volume><target><path>/pool/unrelated.img</path></target></volume>'
        ) as virsh:
            with self.assertRaises(RuntimeError):
                self.cleanup()
        self.assertFalse(any(call.args[0] == 'vol-delete' for call in virsh.call_args_list))

    def test_dependencies_are_refreshed_after_each_deletion(self):
        def response(*args):
            if args[0] == 'vol-dumpxml':
                return f'<volume><target><path>/pool/{args[1]}</path></target></volume>'
            return ''
        with patch.object(migration.BaseImages, 'unused', side_effect=[self.sources, {'/pool/first.img'}, set()]) as unused, patch.object(
            migration, 'stopped'
        ), patch.object(migration, 'virsh', side_effect=response) as virsh:
            with self.assertRaises(RuntimeError):
                self.cleanup()
        self.assertEqual([call.args for call in virsh.call_args_list if call.args[0] == 'vol-delete'],
                         [('vol-delete', 'first.img', '--pool', 'GOAD')])
        self.assertEqual(unused.call_args_list[-1].args[0], {'/pool/second.img'})

    def test_source_aliases_are_canonicalized_before_dependency_inspection(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'original.img'
            source.touch()
            alias = Path(directory) / 'alias.img'
            alias.symlink_to(source)
            self.entries = [{'uuid': 'first', 'source': alias, 'target': Path('/new/first.img')}]
            with patch.object(migration.BaseImages, 'unused', return_value=set()) as unused, patch.object(migration, 'virsh') as virsh:
                with self.assertRaises(RuntimeError):
                    self.cleanup()
            unused.assert_called_once_with({str(source.resolve())}, inspect_domains=True)
            virsh.assert_not_called()
