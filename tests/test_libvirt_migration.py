import importlib.util
from pathlib import Path
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
