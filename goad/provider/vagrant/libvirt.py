import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from goad.log import Log
from goad.provider.vagrant.vagrant import VagrantProvider
from goad.provider.vagrant.libvirt_images import BaseImages, InspectionError
from goad.utils import (
    LIBVIRT,
    PROVISIONING_DOCKER,
    PROVISIONING_LOCAL,
    PROVISIONING_RUNNER,
    PROVISIONING_VM,
)


class LibvirtProvider(VagrantProvider):
    provider_name = LIBVIRT
    default_provisioner = PROVISIONING_LOCAL
    allowed_provisioners = [
        PROVISIONING_LOCAL,
        PROVISIONING_RUNNER,
        PROVISIONING_DOCKER,
        PROVISIONING_VM,
    ]

    DEFAULT_VIRTIO_ISO = Path(
        "/mnt/SSD_DATA/Virtualization/KVM/virtio-win-0.1.271.iso"
    )
    DEFAULT_VIRTIO_ISO_SHA256 = (
        "bbe6166ad86a490caefad438fef8aa494926cb0a1b37fa1212925cfd81656429"
    )
    DEFAULT_VIRTIO_ISO_URL = (
        "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/"
        "archive-virtio/virtio-win-0.1.271-1/virtio-win-0.1.271.iso"
    )
    LIBVIRT_URI = "qemu:///system"
    STORAGE_POOL_PATH = Path("/mnt/SSD_DATA/Virtualization/KVM/GOAD")
    START_BATCH_SIZE = 2
    GUEST_STORAGE_MARKER = r"C:\ProgramData\GOAD\virtio-storage-ready"
    GUEST_PRIVATE_NETWORK_MARKER = (
        r"C:\ProgramData\GOAD\virtio-private-network-ready"
    )

    def check(self):
        checks = [
            self.command.check_vagrant(),
            self.command.check_ram(),
            self.command.check_ansible(),
            self.command.check_vagrant_plugin("vagrant-reload"),
            self.command.check_vagrant_plugin("vagrant-libvirt"),
            self.command.check_libvirt(),
        ]
        return all(checks)

    @property
    def _state_root(self):
        return Path(self.path) / ".goad" / "virtio"

    def _state_dir(self, vm_name):
        return self._state_root / vm_name

    def _marker(self, vm_name, marker_name):
        return self._state_dir(vm_name) / marker_name

    def _state_owner(self):
        return str(Path(self.path).resolve())

    def _validated_cleanup_path(self, value, base_image=False):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise InspectionError('Invalid cleanup path')
        resolved = Path(os.path.realpath(value))
        if resolved.parent != self.STORAGE_POOL_PATH:
            raise InspectionError(f'Cleanup path is outside the GOAD pool: {resolved}')
        if resolved.suffix not in ('.img', '.qcow2'):
            raise InspectionError(f'Unsupported cleanup volume: {resolved}')
        if base_image and (
                resolved.suffix != '.img'
                or '_vagrant_box_image_' not in resolved.name):
            raise InspectionError(f'Invalid Vagrant base image: {resolved}')
        return str(resolved)

    def _read_cleanup_state(self, path, base_images=False):
        try:
            payload = json.loads(path.read_text())
        except (ValueError, OSError) as error:
            raise InspectionError(f'Cannot read cleanup state: {error}') from error
        # Migrate the earlier base-image-only list format after applying the
        # same strict pool/name validation. Legacy disk cleanup lists are not
        # trusted because they were not bound to a workspace owner.
        if isinstance(payload, list) and base_images:
            return {
                self._validated_cleanup_path(value, base_image=True)
                for value in payload
            }
        if (not isinstance(payload, dict)
                or payload.get('owner') != self._state_owner()
                or not isinstance(payload.get('candidates'), list)):
            raise InspectionError('Cleanup state owner or format is invalid')
        return {
            self._validated_cleanup_path(value, base_image=base_images)
            for value in payload['candidates']
        }

    def _write_cleanup_state(self, path, candidates):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps({
            'owner': self._state_owner(),
            'candidates': sorted(candidates),
        }))
        temporary.replace(path)

    def _set_marker(self, vm_name, marker_name):
        marker = self._marker(vm_name, marker_name)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def _clear_marker(self, vm_name, marker_name):
        self._marker(vm_name, marker_name).unlink(missing_ok=True)

    def _normalize_state_dependencies(self):
        if not self._state_root.is_dir():
            return
        for state_dir in self._state_root.iterdir():
            if not state_dir.is_dir():
                continue
            storage_ready = (state_dir / "storage-ready").is_file()
            private_ready = (state_dir / "private-network-ready").is_file()
            if not storage_ready:
                (state_dir / "private-network-ready").unlink(missing_ok=True)
                (state_dir / "network-ready").unlink(missing_ok=True)
            elif not private_ready:
                (state_dir / "network-ready").unlink(missing_ok=True)

    def _has_windows_definition(self):
        try:
            return ':os => "windows"' in (Path(self.path) / "Vagrantfile").read_text()
        except OSError as e:
            Log.error(f"Cannot inspect generated Vagrantfile: {e}")
            return False

    def _virtio_iso(self):
        return Path(os.environ.get("GOAD_VIRTIO_WIN_ISO", self.DEFAULT_VIRTIO_ISO))

    def _download_virtio_iso(self, iso_path, expected_hash):
        url = os.environ.get("GOAD_VIRTIO_WIN_ISO_URL", self.DEFAULT_VIRTIO_ISO_URL)
        digest = hashlib.sha256()
        temp_path = None
        try:
            iso_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".iso.part", dir=iso_path.parent, delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)
                Log.info(f"VirtIO ISO is missing; downloading {url}")
                with urllib.request.urlopen(url, timeout=60) as response:
                    headers = getattr(response, "headers", None)
                    content_length = None if headers is None else headers.get("Content-Length")
                    try:
                        total_size = int(content_length) if content_length else None
                    except (TypeError, ValueError):
                        total_size = None
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        DownloadColumn(),
                        TransferSpeedColumn(),
                        TimeRemainingColumn(),
                        disable=not sys.stderr.isatty(),
                    ) as progress:
                        task = progress.add_task(
                            f"Downloading {iso_path.name}", total=total_size
                        )
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            temp_file.write(chunk)
                            digest.update(chunk)
                            progress.update(task, advance=len(chunk))
                temp_file.flush()
                os.fsync(temp_file.fileno())

            actual_hash = digest.hexdigest()
            if actual_hash.lower() != expected_hash.lower():
                Log.error(
                    "Downloaded VirtIO ISO SHA-256 mismatch: "
                    f"expected {expected_hash}, got {actual_hash}"
                )
                return False
            os.replace(temp_path, iso_path)
            temp_path = None
            Log.success(f"VirtIO ISO downloaded and verified: {iso_path}")
            return True
        except (OSError, urllib.error.URLError) as e:
            Log.error(f"Cannot download VirtIO ISO: {e}")
            return False
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _preflight_virtio_iso(self):
        iso_path = self._virtio_iso()
        expected_hash = os.environ.get("GOAD_VIRTIO_WIN_ISO_SHA256")
        if expected_hash is None and iso_path == self.DEFAULT_VIRTIO_ISO:
            expected_hash = self.DEFAULT_VIRTIO_ISO_SHA256
        if not expected_hash:
            Log.error(
                "GOAD_VIRTIO_WIN_ISO_SHA256 is required when overriding the VirtIO ISO"
            )
            return False

        if not iso_path.is_file():
            if not self._download_virtio_iso(iso_path, expected_hash):
                return False
        if not os.access(iso_path, os.R_OK):
            Log.error(f"VirtIO ISO is unreadable: {iso_path}")
            return False

        try:
            stat = iso_path.stat()
        except OSError as e:
            Log.error(f"Cannot stat VirtIO ISO: {e}")
            return False
        signature = (iso_path, stat.st_size, stat.st_mtime_ns, expected_hash.lower())
        if getattr(self, "_verified_virtio_iso", None) == signature:
            return True

        digest = hashlib.sha256()
        try:
            with iso_path.open("rb") as iso_file:
                for chunk in iter(lambda: iso_file.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as e:
            Log.error(f"Cannot read VirtIO ISO: {e}")
            return False

        actual_hash = digest.hexdigest()
        if actual_hash.lower() != expected_hash.lower():
            Log.error(
                f"VirtIO ISO SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
            )
            return False

        self._verified_virtio_iso = signature
        Log.info(f"VirtIO ISO verified: {iso_path} ({actual_hash})")
        return True

    def _vagrant_result(self, args):
        return self.command.run_vagrant_result(args, self.path)

    def _machine_names(self):
        result = self._vagrant_result(["status", "--machine-readable"])
        if result is None or result.returncode != 0:
            Log.error("Unable to read Vagrant machine status")
            return None

        names = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) >= 4 and row[2] == "state" and row[1] not in names:
                names.append(row[1])
        return names

    def _up_in_batches(self, machine_names):
        """Start at most two guests concurrently to limit host I/O pressure."""
        for offset in range(0, len(machine_names), self.START_BATCH_SIZE):
            batch = machine_names[offset:offset + self.START_BATCH_SIZE]
            if not self.command.run_vagrant(
                ["up"] + batch + ["--provider=libvirt"], self.path
            ):
                return False
        return True

    def _winrm_check(self, vm_name, script):
        result = self._vagrant_result(["winrm", vm_name, "--command", script])
        return result is not None and result.returncode == 0

    def _guest_storage_ready(self, vm_name):
        script = (
            f'if (Test-Path "{self.GUEST_STORAGE_MARKER}") {{ exit 0 }} '
            "else { exit 2 }"
        )
        return self._winrm_check(vm_name, script)

    def _is_windows_machine(self, vm_name):
        result = self._vagrant_result(["winrm-config", vm_name])
        return result is not None and result.returncode == 0

    def _private_network_healthy(self, vm_name):
        script = (
            f'if (-not (Test-Path "{self.GUEST_PRIVATE_NETWORK_MARKER}")) '
            "{ exit 3 }; "
            f'$expected = (Get-Content "{self.GUEST_PRIVATE_NETWORK_MARKER}" '
            "-ErrorAction Stop).Trim().Split('/')[0]; "
            "$address = Get-NetIPAddress -AddressFamily IPv4 "
            "-IPAddress $expected -ErrorAction SilentlyContinue | "
            "Where-Object { "
            "(Get-NetAdapter -InterfaceIndex $_.InterfaceIndex).InterfaceDescription "
            "-match 'VirtIO|Red Hat' } | Select-Object -First 1; "
            "if ($address) { exit 0 } else { exit 4 }"
        )
        return self._winrm_check(vm_name, script)

    def _guest_virtio_healthy(self, vm_name):
        """Validate storage and private networking with one WinRM startup."""
        script = (
            f'if (-not (Test-Path "{self.GUEST_STORAGE_MARKER}")) {{ exit 2 }}; '
            f'if (-not (Test-Path "{self.GUEST_PRIVATE_NETWORK_MARKER}")) '
            "{ exit 3 }; "
            f'$expected = (Get-Content "{self.GUEST_PRIVATE_NETWORK_MARKER}" '
            "-ErrorAction Stop).Trim().Split('/')[0]; "
            "$address = Get-NetIPAddress -AddressFamily IPv4 "
            "-IPAddress $expected -ErrorAction SilentlyContinue | "
            "Where-Object { "
            "(Get-NetAdapter -InterfaceIndex $_.InterfaceIndex).InterfaceDescription "
            "-match 'VirtIO|Red Hat' } | Select-Object -First 1; "
            "if ($address) { exit 0 } else { exit 4 }"
        )
        return self._winrm_check(vm_name, script)

    def _domain_uuid(self, vm_name):
        id_file = Path(self.path) / ".vagrant" / "machines" / vm_name / "libvirt" / "id"
        try:
            domain_uuid = id_file.read_text().strip()
        except OSError as e:
            Log.error(f"Cannot read libvirt domain id for {vm_name}: {e}")
            return None
        return domain_uuid or None

    def _virsh(self, args):
        return self.command.run_process(
            ["virsh", "-c", self.LIBVIRT_URI] + args,
            path=self.path,
        )

    def _dump_domain_xml(self, vm_name):
        domain_uuid = self._domain_uuid(vm_name)
        if domain_uuid is None:
            return None
        result = self._virsh(["dumpxml", domain_uuid, "--inactive"])
        if result is None or result.returncode != 0:
            error = "" if result is None else result.stderr.strip()
            Log.error(f"Cannot dump libvirt XML for {vm_name}: {error}")
            return None
        return result.stdout

    @staticmethod
    def _primary_disk(root):
        for disk in root.findall("./devices/disk[@device='disk']"):
            alias = disk.find("alias")
            if alias is not None and alias.get("name") == "ua-box-volume-0":
                return disk
        return None

    def _storage_xml_is_virtio(self, vm_name):
        xml_text = self._dump_domain_xml(vm_name)
        if xml_text is None:
            return False
        try:
            disk = self._primary_disk(ET.fromstring(xml_text))
        except ET.ParseError:
            return False
        target = None if disk is None else disk.find("target")
        return (
            target is not None
            and target.get("dev") == "vda"
            and target.get("bus") == "virtio"
        )

    def _define_domain_xml(self, vm_name, xml_text):
        state_dir = self._state_dir(vm_name)
        state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", dir=state_dir, delete=False
            ) as temp_file:
                temp_file.write(xml_text)
                temp_path = temp_file.name
            result = self._virsh(["define", temp_path])
            if result is None or result.returncode != 0:
                error = "" if result is None else result.stderr.strip()
                Log.error(f"Cannot define libvirt XML for {vm_name}: {error}")
                return False
            return True
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    def _prepare_storage_transition(self, vm_name):
        xml_text = self._dump_domain_xml(vm_name)
        if xml_text is None:
            return False, None

        state_dir = self._state_dir(vm_name)
        state_dir.mkdir(parents=True, exist_ok=True)
        backup_path = state_dir / "pre-virtio-domain.xml"
        try:
            backup_path.write_text(xml_text)
            root = ET.fromstring(xml_text)
        except (OSError, ET.ParseError) as e:
            Log.error(f"Cannot back up/parse libvirt XML for {vm_name}: {e}")
            return False, None

        primary_disk = self._primary_disk(root)
        if primary_disk is None:
            Log.error(f"Primary disk alias ua-box-volume-0 not found for {vm_name}")
            return False, None

        target = primary_disk.find("target")
        if target is None:
            Log.error(f"Primary disk target not found for {vm_name}")
            return False, None
        target.set("dev", "vda")
        target.set("bus", "virtio")
        address = primary_disk.find("address")
        if address is not None:
            primary_disk.remove(address)

        driver = primary_disk.find("driver")
        if driver is None:
            driver = ET.Element("driver")
            primary_disk.insert(0, driver)
        driver.set("name", "qemu")
        driver.set("cache", "none")
        driver.set("io", "native")
        driver.set("discard", "unmap")
        driver.set("detect_zeroes", "unmap")

        devices = root.find("devices")
        temporary_volume = None
        if devices is not None:
            for disk in list(devices.findall("disk")):
                disk_target = disk.find("target")
                if disk.get("device") == "disk" and disk_target is not None:
                    alias = disk.find("alias")
                    is_bootstrap_disk = (
                        disk_target.get("dev") == "vdb"
                        and alias is not None
                        and alias.get("name", "").startswith("ua-disk-volume-")
                    )
                    if is_bootstrap_disk:
                        source = disk.find("source")
                        if source is not None:
                            temporary_volume = source.get("file")
                        devices.remove(disk)
                elif disk.get("device") == "cdrom":
                    source = disk.find("source")
                    if source is not None and source.get("file") == str(self._virtio_iso()):
                        devices.remove(disk)

        new_xml = ET.tostring(root, encoding="unicode")
        return self._define_domain_xml(vm_name, new_xml), temporary_volume

    def _restore_storage_xml(self, vm_name):
        backup_path = self._state_dir(vm_name) / "pre-virtio-domain.xml"
        try:
            backup_xml = backup_path.read_text()
        except OSError as e:
            Log.error(f"Cannot read rollback XML for {vm_name}: {e}")
            return False
        return self._define_domain_xml(vm_name, backup_xml)

    def _cleanup_temporary_volume(self, vm_name, temporary_volume):
        if not temporary_volume:
            return
        backup_path = self._state_dir(vm_name) / "pre-virtio-domain.xml"
        try:
            backup_root = ET.fromstring(backup_path.read_text())
            devices = backup_root.find("devices")
            if devices is not None:
                for disk in list(devices.findall("disk")):
                    source = disk.find("source")
                    if source is not None and source.get("file") == temporary_volume:
                        devices.remove(disk)
            backup_path.write_text(ET.tostring(backup_root, encoding="unicode"))
        except (OSError, ET.ParseError) as e:
            Log.warning(f"Cannot make rollback XML independent of temporary disk: {e}")
            return
        result = self._virsh(["vol-delete", temporary_volume])
        if result is None or result.returncode != 0:
            Log.warning(f"Temporary VirtIO volume could not be deleted: {temporary_volume}")

    def _rollback_storage(self, vm_name):
        self.command.run_vagrant(["halt", vm_name, "--force"], self.path)
        backup_path = self._state_dir(vm_name) / "pre-virtio-domain.xml"
        restored = self._restore_storage_xml(vm_name) if backup_path.is_file() else True
        self._clear_marker(vm_name, "network-ready")
        self._clear_marker(vm_name, "private-network-ready")
        self._clear_marker(vm_name, "storage-ready")
        if restored:
            self.command.run_vagrant(["up", vm_name, "--provider=libvirt"], self.path)
        return False

    def _start_transitioned_vm(self, vm_name):
        """Start a VM, retrying once when Windows transiently drops WinRM.

        Older Windows boxes can finish booting far enough for Vagrant to mark
        them ready and then briefly restart WinRM while newly enabled VirtIO
        devices settle.  Keep the transitioned domain definition for one clean
        reboot before deciding that the transition itself is broken.
        """
        up_args = ["up", vm_name, "--provider=libvirt"]
        if self.command.run_vagrant(up_args, self.path):
            return True

        Log.warning(
            f"Vagrant/WinRM startup failed for {vm_name}; retrying once after a "
            "forced halt"
        )
        if not self.command.run_vagrant(["halt", vm_name, "--force"], self.path):
            return False
        return self.command.run_vagrant(up_args, self.path)

    def _transition_storage(self, vm_name):
        if self._marker(vm_name, "storage-ready").is_file():
            if self._storage_xml_is_virtio(vm_name) and self._guest_storage_ready(vm_name):
                return True
            # Older GOAD libvirt installs wrote storage-ready after installing
            # the guest driver but deliberately left the OS disk on IDE.  Treat
            # that marker as an upgrade candidate instead of trying to restore
            # a rollback XML which may not exist.
            if self._guest_storage_ready(vm_name):
                Log.info(f"Migrating existing IDE boot disk to VirtIO for {vm_name}")
                self._clear_marker(vm_name, "network-ready")
                self._clear_marker(vm_name, "private-network-ready")
                self._clear_marker(vm_name, "storage-ready")
            else:
                Log.error(f"Stale VirtIO storage state detected for {vm_name}")
                return self._rollback_storage(vm_name)

        if not self.command.run_vagrant(["halt", vm_name], self.path):
            return False
        prepared, temporary_volume = self._prepare_storage_transition(vm_name)
        if not prepared:
            return self._rollback_storage(vm_name)

        self._set_marker(vm_name, "storage-ready")
        if not self._start_transitioned_vm(vm_name):
            return self._rollback_storage(vm_name)
        if not self._guest_storage_ready(vm_name) or not self._storage_xml_is_virtio(vm_name):
            return self._rollback_storage(vm_name)

        self._cleanup_temporary_volume(vm_name, temporary_volume)
        return True

    def _detach_external_virtio_iso(self, vm_name):
        """Keep the shared VirtIO ISO outside vagrant-libvirt's destroy set."""
        if self._domain_uuid(vm_name) is None:
            return True
        xml_text = self._dump_domain_xml(vm_name)
        if xml_text is None:
            return False
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            Log.error(f"Cannot parse libvirt XML for {vm_name} before destroy: {e}")
            return False

        devices = root.find("devices")
        changed = False
        if devices is not None:
            iso_path = str(self._virtio_iso())
            for disk in list(devices.findall("disk[@device='cdrom']")):
                source = disk.find("source")
                if source is not None and source.get("file") == iso_path:
                    devices.remove(disk)
                    changed = True

        if not changed:
            return True
        Log.info(f"Detaching shared VirtIO ISO from {vm_name} before destroy")
        return self._define_domain_xml(
            vm_name, ET.tostring(root, encoding="unicode")
        )

    def _rollback_network_markers(self, vm_name, marker_names):
        self.command.run_vagrant(["halt", vm_name, "--force"], self.path)
        for marker_name in marker_names:
            self._clear_marker(vm_name, marker_name)
        return self.command.run_vagrant(
            ["up", vm_name, "--provider=libvirt"], self.path
        )

    def _transition_network(self, vm_name):
        private_marker = self._marker(vm_name, "private-network-ready")
        network_marker = self._marker(vm_name, "network-ready")
        if network_marker.is_file():
            return self._guest_virtio_healthy(vm_name)

        # A fresh transition enables both VirtIO NICs in one reboot. The private
        # network provisioner runs during that boot and WinRM itself proves that
        # the management NIC is reachable. Keep supporting the old intermediate
        # private-only state so interrupted installations can resume safely.
        markers_to_set = ["network-ready"]
        if not private_marker.is_file():
            markers_to_set.insert(0, "private-network-ready")
        elif not self._private_network_healthy(vm_name):
            Log.error(f"Stale VirtIO private-network state detected for {vm_name}")
            return False

        if not self.command.run_vagrant(["halt", vm_name], self.path):
            return False
        for marker_name in markers_to_set:
            self._set_marker(vm_name, marker_name)
        if not self._start_transitioned_vm(vm_name) or not self._guest_virtio_healthy(vm_name):
            self._rollback_network_markers(vm_name, markers_to_set)
            return False
        return True

    def _saved_base_images(self):
        path = Path(self.path) / '.goad' / 'base-images.json'
        if not path.exists():
            return set()
        return self._read_cleanup_state(path, base_images=True)

    def _make_disks_independent(self, machine_names):
        images = BaseImages(self._virsh)
        try:
            candidates = self._saved_base_images()
            for vm_name in machine_names:
                domain = self._domain_uuid(vm_name)
                if domain is None:
                    raise InspectionError(f'Cannot find domain for {vm_name}')
                candidates.update(images.candidates(images.output('dumpxml', domain)))
                # Save before changing the disk so interrupted conversions and
                # later destroy calls can still identify the original bases.
                state = Path(self.path) / '.goad' / 'base-images.json'
                validated = {
                    self._validated_cleanup_path(candidate, base_image=True)
                    for candidate in candidates
                }
                self._write_cleanup_state(state, validated)
                Log.info(f'Creating independent disk for {vm_name}; this can take several minutes')
                images.flatten(domain)
        except (InspectionError, ET.ParseError, OSError) as error:
            Log.error(f'Independent disk conversion failed: {error}')
            return False
        try:
            for image in images.cleanup(candidates, inspect_domains=True):
                Log.info(f'Deleted unused base image: {image}')
        except (InspectionError, OSError) as error:
            Log.warning(f'Independent disks ready, but base images retained: {error}')
        return True

    def install(self):
        if self._has_windows_definition() and not self._preflight_virtio_iso():
            return False
        self._normalize_state_dependencies()
        if not self.command.refresh_libvirt_pool():
            return False
        machine_names = self._machine_names()
        if machine_names is None:
            return False
        if not self._up_in_batches(machine_names):
            return False
        for vm_name in machine_names:
            if not self._is_windows_machine(vm_name):
                continue
            if not self._guest_storage_ready(vm_name):
                Log.error(f"VirtIO guest marker is missing for Windows VM {vm_name}")
                return False
            if not self._transition_storage(vm_name):
                return False
            if not self._transition_network(vm_name):
                return False
        return self._make_disks_independent(machine_names)

    def _destroy_with_image_cleanup(self, machine_names, args):
        images = BaseImages(self._virsh)
        state = Path(self.path) / '.goad' / 'disk-cleanup.json'
        candidates = set()
        try:
            if state.exists():
                candidates.update(self._read_cleanup_state(state))
            candidates.update(self._saved_base_images())
            for vm_name in machine_names:
                id_file = Path(self.path) / '.vagrant' / 'machines' / vm_name / 'libvirt' / 'id'
                if id_file.is_file():
                    xml = self._dump_domain_xml(vm_name)
                    if xml is None:
                        raise InspectionError(f'Cannot inspect disks for {vm_name}')
                    candidates.update(images.guest_volumes(xml))
                    candidates.update(images.candidates(xml))
            # Persist before Vagrant removes domain metadata, also for retries.
            validated = {
                self._validated_cleanup_path(candidate)
                for candidate in candidates
            }
            self._write_cleanup_state(state, validated)
        except (InspectionError, ET.ParseError, OSError, ValueError) as error:
            Log.warning(f'Image cleanup skipped: {error}')
            candidates.clear()
        for vm_name in machine_names:
            if not self._detach_external_virtio_iso(vm_name):
                Log.error(f"Refusing to destroy {vm_name} while its shared ISO is attached")
                return False
        destroyed = self.command.run_vagrant(args, self.path)
        if destroyed:
            for vm_name in machine_names:
                shutil.rmtree(self._state_dir(vm_name), ignore_errors=True)
            try:
                for image in images.cleanup(candidates, inspect_domains=True):
                    Log.info(f'Deleted unused image: {image}')
            except (InspectionError, OSError) as error:
                Log.warning(f'Images retained: {error}')
        return destroyed

    def destroy(self):
        machine_names = self._machine_names()
        if machine_names is None:
            return False
        destroyed = self._destroy_with_image_cleanup(machine_names, ["destroy"])
        if destroyed:
            shutil.rmtree(self._state_root, ignore_errors=True)
        return destroyed

    def destroy_vm(self, vm_name):
        return self._destroy_with_image_cleanup([vm_name], ["destroy", vm_name])

    def start(self):
        if not self.command.refresh_libvirt_pool():
            return False
        machine_names = self._machine_names()
        return machine_names is not None and self._up_in_batches(machine_names)

    def start_vm(self, vm_name):
        if not self.command.refresh_libvirt_pool():
            return False
        return self.command.run_vagrant(
            ["up", vm_name, "--provider=libvirt"], self.path
        )
