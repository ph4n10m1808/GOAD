import unittest
import hashlib
import io
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from goad.provider.vagrant.libvirt import LibvirtProvider


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeCommand:
    def __init__(self):
        self.calls = []

    def refresh_libvirt_pool(self):
        self.calls.append(("pool-refresh", None))
        return True

    def run_vagrant(self, args, path):
        self.calls.append((args, path))
        return True

    def run_vagrant_result(self, args, path, capture_output=True):
        self.calls.append((args, path))
        if args == ["status", "--machine-readable"]:
            return SimpleNamespace(returncode=0, stdout="1,linux,state,running\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="not a WinRM guest")


class LibvirtProviderTest(unittest.TestCase):
    def test_every_vagrant_provider_configures_windows_vietnam_timezone(self):
        for provider_name in ("libvirt", "virtualbox", "vmware", "vmware_esxi"):
            vagrantfile = PROJECT_ROOT / "template" / "provider" / provider_name / "Vagrantfile"
            content = vagrantfile.read_text()
            self.assertIn("Configure-TimeZone.ps1", content, provider_name)
            self.assertIn('run: "always"', content, provider_name)

    def test_libvirt_windows_private_adapter_has_persistent_gateway_and_soc_route(self):
        template = (
            PROJECT_ROOT / "template/provider/libvirt/Vagrantfile"
        ).read_text()
        provisioner = (
            PROJECT_ROOT / "vagrant/Configure-VirtIOPrivateNetwork.ps1"
        ).read_text()
        soc_provisioner = (
            PROJECT_ROOT / "vagrant/Configure-SocPrivateNetwork.ps1"
        ).read_text()

        self.assertIn("GOAD_SOC_NETWORK", template)
        self.assertIn(
            '(box.has_key?(:soc_ip) ? "" : soc_network_prefix),',
            template,
        )
        self.assertIn('(box.has_key?(:soc_mac) ? box[:soc_mac] : "")]', template)
        self.assertIn('if box.has_key?(:soc_ip) && network_ready', template)
        self.assertIn('$normalizedExcludedMac', provisioner)
        self.assertIn('Set-PersistentRoute -Prefix "0.0.0.0/0" -Destination "0.0.0.0" -Mask "0.0.0.0"', provisioner)
        self.assertIn('"192.168.50.0/24"', provisioner)
        self.assertIn("route.exe -p ADD", provisioner)
        self.assertIn("route.exe PRINT -4", provisioner)
        self.assertIn("Persistent Routes:", provisioner)
        self.assertIn("$defaultGatewayMetric = 1000", provisioner)
        self.assertIn("MASK $Mask $GatewayAddress `", provisioner)
        self.assertIn("METRIC $Metric IF $adapter.ifIndex", provisioner)
        self.assertIn("box[:soc_gateway]", template)
        self.assertIn("$socGatewayMetric = 2000", soc_provisioner)
        self.assertIn("route.exe -p ADD", soc_provisioner)
        self.assertIn("METRIC $socGatewayMetric IF $adapter.ifIndex", soc_provisioner)
        self.assertIn("RegisterThisConnectionsAddress $false", soc_provisioner)
        self.assertIn("UseSuffixWhenRegistering $false", soc_provisioner)

    def test_install_selects_libvirt_explicitly(self):
        with tempfile.TemporaryDirectory() as provider_path:
            Path(provider_path, "Vagrantfile").write_text(':os => "linux"')
            provider = LibvirtProvider("GOAD-Light")
            provider.command = FakeCommand()
            provider.path = provider_path

            provider._make_disks_independent = lambda names: names == ['linux']
            self.assertTrue(provider.install())
            self.assertEqual(provider.command.calls[0], ("pool-refresh", None))
            self.assertIn(
                (["up", "linux", "--provider=libvirt"], provider_path),
                provider.command.calls,
            )
            self.assertIn(
                (["status", "--machine-readable"], provider_path),
                provider.command.calls,
            )

    def test_every_lab_has_physical_libvirt_files(self):
        for lab_dir in (PROJECT_ROOT / "ad").iterdir():
            virtualbox_dir = lab_dir / "providers" / "virtualbox"
            if not virtualbox_dir.is_dir():
                continue
            libvirt_dir = lab_dir / "providers" / "libvirt"
            self.assertTrue((libvirt_dir / "Vagrantfile").is_file(), lab_dir.name)
            self.assertTrue((libvirt_dir / "inventory").is_file(), lab_dir.name)

    def test_every_virtualbox_extension_has_libvirt_manifest(self):
        for extension_dir in (PROJECT_ROOT / "extensions").iterdir():
            virtualbox_file = extension_dir / "providers" / "virtualbox" / "Vagrantfile"
            if not virtualbox_file.is_file():
                continue
            libvirt_file = extension_dir / "providers" / "libvirt" / "Vagrantfile"
            self.assertTrue(libvirt_file.is_file(), extension_dir.name)

    def test_every_lab_vagrantfile_renders_as_valid_ruby(self):
        provider_environment = Environment(
            loader=FileSystemLoader(PROJECT_ROOT / "template" / "provider" / "libvirt")
        )
        for lab_dir in (PROJECT_ROOT / "ad").iterdir():
            manifest = lab_dir / "providers" / "libvirt" / "Vagrantfile"
            if not manifest.is_file() or lab_dir.name == "TEMPLATE":
                continue

            lab_environment = Environment(loader=FileSystemLoader(manifest.parent))
            lab = lab_environment.get_template("Vagrantfile").render(
                lab_name=lab_dir.name,
                ip_range="192.168.56",
            )
            rendered = provider_environment.get_template("Vagrantfile").render(
                lab_name=lab_dir.name,
                lab=lab,
                extensions="",
                ip_range="192.168.56",
                use_provisioning_vm=False,
            )

            self.assertIn("VAGRANT_DEFAULT_PROVIDER'] = 'libvirt'", rendered)
            self.assertNotIn("VAGRANT_NO_PARALLEL", rendered)
            self.assertIn("libvirt.storage_pool_name = 'GOAD'", rendered)
            self.assertIn("libvirt.snapshot_pool_name = 'GOAD'", rendered)
            self.assertNotIn("GOAD_LIBVIRT_COMPATIBILITY", rendered)
            self.assertIn("GOAD_VIRTIO_WIN_ISO", rendered)
            self.assertIn('libvirt.cpu_mode = "host-passthrough"', rendered)
            self.assertIn(
                "libvirt.cputopology :sockets => 1, :cores => box[:cpus], :threads => 1",
                rendered,
            )
            self.assertNotIn('libvirt.graphics_type = "none"', rendered)
            self.assertNotIn('libvirt.video_type = "none"', rendered)
            self.assertIn("storage-ready", rendered)
            self.assertIn('libvirt.disk_bus = "virtio"', rendered)
            self.assertIn('libvirt.disk_bus = "ide"', rendered)
            self.assertIn('libvirt.disk_device = "hda"', rendered)
            self.assertIn('elsif storage_ready\n          libvirt.nic_model_type = private_network_ready ? "virtio" : "e1000"\n          libvirt.management_network_model_type = network_ready ? "virtio" : "e1000"\n          # storage-ready is written only after the in-guest driver check and\n          # the boot disk transition both succeed.\n          libvirt.disk_bus = "virtio"', rendered)
            self.assertIn("libvirt.disk_driver :cache => 'none', :io => 'native'", rendered)
            self.assertIn('libvirt.nic_model_type = "virtio"', rendered)
            self.assertIn('libvirt.management_network_model_type = "e1000"', rendered)
            self.assertIn('libvirt__driver_queues:', rendered)
            self.assertIn("virtio_queues = [box[:cpus].to_i, 1].max", rendered)
            self.assertIn("Install-VirtIO.ps1", rendered)
            self.assertIn(
                'Install-VirtIO.ps1", privileged: false',
                rendered,
            )
            self.assertIn("Configure-VirtIOPrivateNetwork.ps1", rendered)
            self.assertIn("if private_network_ready", rendered)
            self.assertNotIn("if private_network_ready && !network_ready", rendered)
            self.assertIn(
                '(box.has_key?(:soc_ip) ? "" : soc_network_prefix),',
                rendered,
            )
            self.assertIn('(box.has_key?(:soc_mac) ? box[:soc_mac] : "")]', rendered)
            self.assertIn('run: "always"', rendered)
            self.assertIn("Configure-TimeZone.ps1", rendered)
            self.assertIn("config.winrm.max_tries = 60", rendered)
            self.assertIn("config.winrm.timeout = 600", rendered)
            self.assertIn("config.vm.boot_timeout = 1200", rendered)
            self.assertNotIn("config.winrm.retry_limit", rendered)
            self.assertIn("clock_timer :name => 'hypervclock'", rendered)
            self.assertIn("hyperv_feature :name => 'vpindex'", rendered)
            self.assertNotIn('{{', rendered)
            syntax = subprocess.run(
                ["ruby", "-c"],
                input=rendered,
                capture_output=True,
                text=True,
            )
            self.assertEqual(syntax.returncode, 0, f"{lab_dir.name}: {syntax.stderr}")

            if lab_dir.name == "GOAD-Light":
                self.assertEqual(rendered.count(':soc_network => "soc-dedicated-es-private"'), 3)
                self.assertEqual(rendered.count(':soc_gateway => "192.168.50.1"'), 3)
                for address in ("192.168.50.20", "192.168.50.21", "192.168.50.22"):
                    self.assertEqual(rendered.count(f':soc_ip => "{address}"'), 1)
                for mac in ("52:54:00:50:20:20", "52:54:00:50:20:21", "52:54:00:50:20:22"):
                    self.assertEqual(rendered.count(f':soc_mac => "{mac}"'), 1)
                self.assertIn('if box.has_key?(:soc_ip) && network_ready', rendered)
                self.assertIn('libvirt__network_name: box[:soc_network]', rendered)
            else:
                self.assertNotIn(':soc_ip =>', rendered)

    def test_box_converter_defers_disk_bus_to_generated_lab(self):
        converter = (
            PROJECT_ROOT / "scripts" / "convert_vagrant_box_to_libvirt.py"
        ).read_text()
        self.assertNotIn("GOAD_LIBVIRT_COMPATIBILITY", converter)
        self.assertIn("selects the disk bus per guest/state", converter)

    def test_storage_transition_changes_only_primary_disk_and_detaches_bootstrap_devices(self):
        domain_xml = """
        <domain><devices>
          <disk type="file" device="disk">
            <driver name="qemu" type="qcow2"/>
            <source file="/pool/boot.qcow2"/>
            <target dev="hda" bus="ide"/>
            <alias name="ua-box-volume-0"/>
            <address type="drive"/>
          </disk>
          <disk type="file" device="disk">
            <source file="/pool/bootstrap.qcow2"/>
            <target dev="vdb" bus="virtio"/>
            <alias name="ua-disk-volume-0"/>
          </disk>
          <disk type="file" device="cdrom">
            <source file="/virtio.iso"/>
            <target dev="hdc" bus="ide"/>
          </disk>
        </devices></domain>
        """
        with tempfile.TemporaryDirectory() as provider_path:
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            provider._dump_domain_xml = lambda vm_name: domain_xml
            provider._virtio_iso = lambda: Path("/virtio.iso")
            captured = {}

            def capture_xml(vm_name, xml_text):
                captured["xml"] = xml_text
                return True

            provider._define_domain_xml = capture_xml
            success, temporary_volume = provider._prepare_storage_transition("DC01")

            self.assertTrue(success)
            self.assertEqual(temporary_volume, "/pool/bootstrap.qcow2")
            root = ET.fromstring(captured["xml"])
            disks = root.findall("./devices/disk")
            self.assertEqual(len(disks), 1)
            primary = disks[0]
            self.assertEqual(primary.find("target").attrib, {"dev": "vda", "bus": "virtio"})
            self.assertIsNone(primary.find("address"))
            driver = primary.find("driver")
            self.assertEqual(driver.get("cache"), "none")
            self.assertEqual(driver.get("io"), "native")
            self.assertTrue(
                Path(provider_path, ".goad", "virtio", "DC01", "pre-virtio-domain.xml").is_file()
            )

    def test_network_transition_enables_both_virtio_nics_with_one_reboot(self):
        with tempfile.TemporaryDirectory() as provider_path:
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            provider.command = FakeCommand()
            provider._set_marker("DC01", "storage-ready")
            provider._guest_virtio_healthy = lambda vm_name: True

            self.assertTrue(provider._transition_network("DC01"))
            self.assertTrue(provider._marker("DC01", "private-network-ready").is_file())
            self.assertTrue(provider._marker("DC01", "network-ready").is_file())
            lifecycle_calls = [
                call[0] for call in provider.command.calls if isinstance(call[0], list)
            ]
            self.assertEqual(
                lifecycle_calls,
                [
                    ["halt", "DC01"],
                    ["up", "DC01", "--provider=libvirt"],
                ],
            )

    def test_install_transitions_windows_boot_disk_after_driver_check(self):
        with tempfile.TemporaryDirectory() as provider_path:
            Path(provider_path, "Vagrantfile").write_text(':os => "windows"')
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            provider.command = FakeCommand()
            provider._preflight_virtio_iso = lambda: True
            provider._machine_names = lambda: ["DC01"]
            provider._is_windows_machine = lambda vm_name: True
            provider._guest_storage_ready = lambda vm_name: True
            transitioned = []
            provider._transition_storage = lambda vm_name: transitioned.append(vm_name) or True
            provider._transition_network = lambda vm_name: True
            provider._make_disks_independent = lambda names: names == ['DC01']

            self.assertTrue(provider.install())
            self.assertEqual(transitioned, ["DC01"])

    def test_stale_ide_marker_is_upgraded_instead_of_rolled_back(self):
        with tempfile.TemporaryDirectory() as provider_path:
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            provider.command = FakeCommand()
            provider._set_marker("DC01", "storage-ready")
            provider._set_marker("DC01", "private-network-ready")
            provider._set_marker("DC01", "network-ready")
            storage_checks = iter((False, True))
            provider._storage_xml_is_virtio = lambda vm_name: next(storage_checks)
            provider._guest_storage_ready = lambda vm_name: True
            provider._prepare_storage_transition = lambda vm_name: (True, None)
            provider._start_transitioned_vm = lambda vm_name: True

            self.assertTrue(provider._transition_storage("DC01"))
            self.assertTrue(provider._marker("DC01", "storage-ready").is_file())
            self.assertFalse(provider._marker("DC01", "private-network-ready").exists())
            self.assertFalse(provider._marker("DC01", "network-ready").exists())

    def test_destroy_clears_all_virtio_state_for_clean_rebuild(self):
        with tempfile.TemporaryDirectory() as provider_path:
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            provider.command = FakeCommand()
            provider._set_marker("DC01", "storage-ready")
            provider._set_marker("DC02", "network-ready")
            provider._machine_names = lambda: ["DC01", "DC02"]
            provider._detach_external_virtio_iso = lambda vm_name: True

            self.assertTrue(provider.destroy())
            self.assertFalse(provider._state_root.exists())
            self.assertIn((["destroy"], provider_path), provider.command.calls)

    def test_destroy_vm_clears_only_that_guests_virtio_state(self):
        with tempfile.TemporaryDirectory() as provider_path:
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            provider.command = FakeCommand()
            provider._set_marker("DC01", "storage-ready")
            provider._set_marker("DC02", "storage-ready")
            provider._detach_external_virtio_iso = lambda vm_name: True

            self.assertTrue(provider.destroy_vm("DC01"))
            self.assertFalse(provider._state_dir("DC01").exists())
            self.assertTrue(provider._state_dir("DC02").exists())
            self.assertIn(
                (["destroy", "DC01"], provider_path), provider.command.calls
            )

    def test_destroy_tracks_auxiliary_disks_and_retries_cleanup(self):
        import json
        from unittest.mock import Mock
        from goad.provider.vagrant.libvirt_images import InspectionError
        for whole_lab in (False, True):
            with self.subTest(whole_lab=whole_lab), tempfile.TemporaryDirectory() as provider_path:
                provider = LibvirtProvider('GOAD')
                provider.path = provider_path
                provider.STORAGE_POOL_PATH = Path('/pool')
                provider.command = FakeCommand()
                provider._machine_names = lambda: ['DC01']
                provider._detach_external_virtio_iso = lambda name: True
                provider._dump_domain_xml = lambda name: '<domain/>'
                id_file = Path(provider_path) / '.vagrant/machines/DC01/libvirt/id'
                id_file.parent.mkdir(parents=True)
                id_file.write_text('uuid')
                candidates = {'/pool/lab-DC01.img', '/pool/lab-DC01-vdb.qcow2',
                              '/pool/windows_vagrant_box_image_1.img'}
                images = Mock()
                images.guest_volumes.return_value = candidates - {'/pool/windows_vagrant_box_image_1.img'}
                images.candidates.return_value = {'/pool/windows_vagrant_box_image_1.img'}
                images.cleanup.side_effect = InspectionError('pool unavailable')
                state = Path(provider_path) / '.goad/disk-cleanup.json'
                def destroy(args, path):
                    payload = json.loads(state.read_text())
                    self.assertEqual(set(payload['candidates']), candidates)
                    self.assertEqual(payload['owner'], str(Path(provider_path).resolve()))
                    id_file.unlink(missing_ok=True)
                    return True
                provider.command.run_vagrant = destroy
                with patch('goad.provider.vagrant.libvirt.BaseImages', return_value=images):
                    action = provider.destroy if whole_lab else lambda: provider.destroy_vm('DC01')
                    self.assertTrue(action())
                    images.cleanup.assert_called_once_with(candidates, inspect_domains=True)
                    images.cleanup.reset_mock(side_effect=True)
                    images.cleanup.return_value = sorted(candidates)
                    self.assertTrue(action())
                    images.cleanup.assert_called_once_with(candidates, inspect_domains=True)
                    # Metadata disappeared after the first destroy; retry uses saved paths.
                    self.assertEqual(images.guest_volumes.call_count, 1)

    def test_failed_destroy_does_not_delete_images(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as provider_path:
            provider = LibvirtProvider('GOAD')
            provider.path = provider_path
            provider.command = Mock()
            provider.command.run_vagrant.return_value = False
            provider._detach_external_virtio_iso = lambda name: True
            with patch('goad.provider.vagrant.libvirt.BaseImages') as images:
                self.assertFalse(provider.destroy_vm('DC01'))
                images.return_value.cleanup.assert_not_called()

    def test_destroy_detaches_shared_iso_from_domain_xml(self):
        domain_xml = """
        <domain><devices>
          <disk type="file" device="disk"><source file="/pool/os.img"/></disk>
          <disk type="file" device="cdrom"><source file="/virtio.iso"/></disk>
        </devices></domain>
        """
        provider = LibvirtProvider("GOAD")
        provider._domain_uuid = lambda vm_name: "uuid"
        provider._dump_domain_xml = lambda vm_name: domain_xml
        provider._virtio_iso = lambda: Path("/virtio.iso")
        captured = {}
        provider._define_domain_xml = lambda vm_name, xml: captured.setdefault("xml", xml) is not None

        self.assertTrue(provider._detach_external_virtio_iso("DC01"))
        root = ET.fromstring(captured["xml"])
        self.assertEqual(root.findall("./devices/disk[@device='cdrom']"), [])
        self.assertEqual(len(root.findall("./devices/disk[@device='disk']")), 1)

    def test_transition_start_retries_once_after_transient_winrm_failure(self):
        class RetryCommand(FakeCommand):
            def __init__(self):
                super().__init__()
                self.up_attempts = 0

            def run_vagrant(self, args, path):
                self.calls.append((args, path))
                if args[:2] == ["up", "DC03"]:
                    self.up_attempts += 1
                    return self.up_attempts == 2
                return True

        provider = LibvirtProvider("GOAD")
        provider.path = "/provider"
        provider.command = RetryCommand()

        self.assertTrue(provider._start_transitioned_vm("DC03"))
        self.assertEqual(
            [call[0] for call in provider.command.calls],
            [
                ["up", "DC03", "--provider=libvirt"],
                ["halt", "DC03", "--force"],
                ["up", "DC03", "--provider=libvirt"],
            ],
        )

    def test_start_refreshes_storage_pool(self):
        provider = LibvirtProvider("GOAD")
        provider.path = "/provider"
        provider.command = FakeCommand()

        self.assertTrue(provider.start())
        self.assertTrue(provider.start_vm("DC01"))
        self.assertEqual(provider.command.calls.count(("pool-refresh", None)), 2)

    def test_start_limits_parallel_vagrant_actions_to_two_guests(self):
        provider = LibvirtProvider("GOAD")
        provider.path = "/provider"
        provider.command = FakeCommand()
        provider._machine_names = lambda: ["DC01", "DC02", "DC03", "SRV02", "SRV03"]

        self.assertTrue(provider.start())
        self.assertEqual(
            provider.command.calls,
            [
                ("pool-refresh", None),
                (["up", "DC01", "DC02", "--provider=libvirt"], "/provider"),
                (["up", "DC03", "SRV02", "--provider=libvirt"], "/provider"),
                (["up", "SRV03", "--provider=libvirt"], "/provider"),
            ],
        )

    def test_virtio_iso_is_scanned_once_and_cached(self):
        payload = (
            b"header-virtio-win-guest-tools.exe-middle-"
            b"virtio-win-gt-x64.msi-footer"
        )
        with tempfile.TemporaryDirectory() as provider_path:
            iso_path = Path(provider_path, "virtio.iso")
            iso_path.write_bytes(payload)
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            reads = 0
            original_open = Path.open

            def counting_open(path, *args, **kwargs):
                nonlocal reads
                if path == iso_path and "r" in kwargs.get("mode", args[0] if args else "r"):
                    reads += 1
                return original_open(path, *args, **kwargs)

            env = {
                "GOAD_VIRTIO_WIN_ISO": str(iso_path),
                "GOAD_VIRTIO_WIN_ISO_SHA256": hashlib.sha256(payload).hexdigest(),
            }
            with patch.dict(os.environ, env), patch.object(Path, "open", counting_open):
                self.assertTrue(provider._preflight_virtio_iso())
                self.assertTrue(provider._preflight_virtio_iso())

            self.assertEqual(reads, 1)

    def test_missing_virtio_iso_is_downloaded_atomically_and_verified(self):
        payload = (
            b"header-virtio-win-guest-tools.exe-middle-"
            b"virtio-win-gt-x64.msi-footer"
        )
        with tempfile.TemporaryDirectory() as provider_path:
            iso_path = Path(provider_path, "downloads", "virtio.iso")
            provider = LibvirtProvider("GOAD")
            provider.path = provider_path
            env = {
                "GOAD_VIRTIO_WIN_ISO": str(iso_path),
                "GOAD_VIRTIO_WIN_ISO_SHA256": hashlib.sha256(payload).hexdigest(),
                "GOAD_VIRTIO_WIN_ISO_URL": "https://example.test/virtio.iso",
            }
            with patch.dict(os.environ, env), patch(
                "urllib.request.urlopen", return_value=io.BytesIO(payload)
            ) as download:
                self.assertTrue(provider._preflight_virtio_iso())

            download.assert_called_once_with(env["GOAD_VIRTIO_WIN_ISO_URL"], timeout=60)
            self.assertEqual(iso_path.read_bytes(), payload)
            self.assertEqual(list(iso_path.parent.glob("*.part")), [])

    def test_invalid_download_does_not_replace_iso_target(self):
        payload = b"not-the-expected-iso"
        with tempfile.TemporaryDirectory() as provider_path:
            iso_path = Path(provider_path, "virtio.iso")
            provider = LibvirtProvider("GOAD")
            env = {
                "GOAD_VIRTIO_WIN_ISO": str(iso_path),
                "GOAD_VIRTIO_WIN_ISO_SHA256": "0" * 64,
            }
            with patch.dict(os.environ, env), patch(
                "urllib.request.urlopen", return_value=io.BytesIO(payload)
            ):
                self.assertFalse(provider._preflight_virtio_iso())

            self.assertFalse(iso_path.exists())
            self.assertEqual(list(Path(provider_path).glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
