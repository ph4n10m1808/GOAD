"""Conservative collection of unused local vagrant-libvirt base volumes."""
import os
import json
import subprocess
import tempfile
import uuid
import re
import xml.etree.ElementTree as ET
from pathlib import Path


class InspectionError(RuntimeError):
    pass


class BaseImages:
    def __init__(self, virsh):
        self.virsh = virsh

    def output(self, *args):
        result = self.virsh(list(args))
        if result is None or result.returncode != 0:
            raise InspectionError(f"Cannot inspect libvirt: {' '.join(args)}")
        return result.stdout

    def xml(self, *args):
        try:
            return ET.fromstring(self.output(*args))
        except ET.ParseError as error:
            raise InspectionError('Invalid libvirt XML') from error

    @staticmethod
    def path(value):
        if not value or not os.path.isabs(value):
            raise InspectionError('Missing or non-absolute storage path')
        return os.path.realpath(value)

    def candidates(self, domain_xml):
        """Only consider base images actually backing this instance's disks."""
        result = set()
        root = ET.fromstring(domain_xml)
        for disk in root.findall("./devices/disk[@device='disk']"):
            source = disk.find('source')
            if source is None or not source.get('file'):
                raise InspectionError('Unsupported guest disk source')
            volume = self.xml('vol-dumpxml', self.path(source.get('file')))
            backing = volume.findtext('./backingStore/path')
            if backing:
                backing = self.path(backing)
                if re.fullmatch(r'.+_vagrant_box_image_.+\.img', Path(backing).name):
                    result.add(backing)
        return result

    def guest_volumes(self, domain_xml):
        """Capture only Vagrant-named local disks owned by this domain."""
        root = ET.fromstring(domain_xml)
        name = root.findtext('name')
        if not name:
            raise InspectionError('Missing domain name')
        result = set()
        for disk in root.findall("./devices/disk[@device='disk']"):
            source = disk.find('source')
            if source is None or not source.get('file'):
                continue
            path = self.path(source.get('file'))
            filename = Path(path).name
            if (filename in (name + '.img', name + '.qcow2')
                    or re.fullmatch(re.escape(name) + r'-vd[a-z]+\.(?:img|qcow2)', filename)):
                self.xml('vol-dumpxml', path)
                result.add(path)
        return result

    def external_chain(self, path):
        """Inspect local disks outside pools; never infer independence from XML."""
        try:
            result = subprocess.run(
                ['qemu-img', 'info', '--output=json', '--backing-chain', path],
                capture_output=True, text=True, check=True, timeout=30)
            chain = json.loads(result.stdout)
            if not isinstance(chain, list) or not chain:
                raise ValueError('Missing backing chain')
            return {self.path(item['filename']) for item in chain}
        except (OSError, subprocess.SubprocessError):
            # libvirt can read root-owned disks even when the GOAD user cannot.
            return self._external_chain_via_libvirt(path)
        except (ValueError, KeyError, TypeError) as error:
            raise InspectionError(f'Cannot inspect external disk {path}: {error}') from error

    def _external_chain_via_libvirt(self, path):
        """Inspect existing files through temporary directory pools, without building them."""
        references = set()
        while path:
            path = self.path(path)
            if path in references:
                raise InspectionError(f'Cyclic backing chain at {path}')
            references.add(path)
            # Prefer an existing volume registration for subsequent chain members.
            result = self.virsh(['vol-dumpxml', path])
            if result is not None and result.returncode == 0:
                try:
                    volume = ET.fromstring(result.stdout)
                except ET.ParseError as error:
                    raise InspectionError('Invalid volume XML') from error
            else:
                name = 'goad-inspect-' + uuid.uuid4().hex
                pool = ET.Element('pool', type='dir')
                ET.SubElement(pool, 'name').text = name
                target = ET.SubElement(pool, 'target')
                ET.SubElement(target, 'path').text = str(Path(path).parent)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.xml') as definition:
                    definition.write(ET.tostring(pool, encoding='unicode'))
                    definition.flush()
                    # No pool-build, autostart, persistent definition or file mutation.
                    self.output('pool-create', definition.name)
                    try:
                        volume = self.xml('vol-dumpxml', Path(path).name, '--pool', name)
                    finally:
                        self.output('pool-destroy', name)
            if self.path(volume.findtext('./target/path')) != path:
                raise InspectionError(f'Unexpected volume path while inspecting {path}')
            if volume.find('./target/format') is None:
                raise InspectionError(f'Missing disk format for {path}')
            path = volume.findtext('./backingStore/path')
        return references

    def flatten(self, domain):
        """Populate running guest disks in place without changing their paths."""
        if self.output('snapshot-list', domain, '--name').strip():
            raise InspectionError('Cannot flatten a guest with snapshots')
        root = self.xml('dumpxml', domain)
        candidates = self.candidates(ET.tostring(root, encoding='unicode'))
        for disk in root.findall("./devices/disk[@device='disk']"):
            source = disk.find('source')
            if source is None or not source.get('file'):
                raise InspectionError('Unsupported guest disk source')
            volume = self.xml('vol-dumpxml', self.path(source.get('file')))
            if not volume.findtext('./backingStore/path'):
                continue
            driver = disk.find('driver')
            target = disk.find('target')
            if (source is None or not source.get('file') or driver is None
                    or driver.get('type') != 'qcow2' or target is None
                    or not target.get('dev')):
                raise InspectionError('Only local QCOW2 disks can be flattened')
            self.output('blockpull', domain, target.get('dev'), '--wait', '--verbose')
        for args in [('dumpxml', domain), ('dumpxml', domain, '--inactive')]:
            verified = self.xml(*args)
            if verified.findall("./devices/disk[@device='disk']/backingStore/source"):
                raise InspectionError('Guest still has a backing chain after blockpull')
        for disk in root.findall("./devices/disk[@device='disk']"):
            source = disk.find('source')
            volume = self.xml('vol-dumpxml', self.path(source.get('file')))
            if volume.findtext('./backingStore/path'):
                raise InspectionError('Disk volume still has a backing file after blockpull')
        return candidates

    def unused(self, candidates, inspect_domains=False):
        if not candidates:
            return set()
        # Remaining domains may have saved state, snapshots or disks outside
        # managed pools. Keep shared bases until no domains remain on this URI.
        domains = self.output('list', '--all', '--uuid').split()
        if domains and not inspect_domains:
            raise InspectionError('Other libvirt domains remain; keeping shared base images')
        sources = set()
        if domains:
            if self.output('list', '--all', '--with-managed-save', '--uuid').strip():
                raise InspectionError('Saved guest state may reference base images')
            for domain in domains:
                if self.output('snapshot-list', domain, '--name').strip():
                    raise InspectionError('Guest snapshots may reference base images')
                for args in [('dumpxml', domain), ('dumpxml', domain, '--inactive')]:
                    root = self.xml(*args)
                    for disk in root.findall("./devices/disk[@device='disk']"):
                        for source in disk.findall('.//source'):
                            if not source.get('file'):
                                raise InspectionError('Unsupported disk source')
                            sources.add(self.path(source.get('file')))
        if self.output('pool-list', '--inactive', '--name').strip():
            raise InspectionError('Inactive pools cannot be checked for dependent disks')
        references = set()
        volumes = set()
        for pool in self.output('pool-list', '--name').splitlines():
            if not pool.strip():
                continue
            if self.xml('pool-dumpxml', pool).get('type') not in ('dir', 'fs', 'netfs'):
                raise InspectionError('Unsupported storage pool; keeping base images')
            self.output('pool-refresh', pool)
            listing = self.output('vol-list', pool)
            rows = listing.splitlines()
            separator = next((i for i, row in enumerate(rows) if re.fullmatch(r'\s*-+\s*', row)), None)
            if separator is None:
                raise InspectionError('Unrecognized volume listing')
            for row in rows[separator + 1:]:
                if not row.strip():
                    continue
                columns = re.split(r'\s{2,}', row.strip())
                if len(columns) != 2:
                    raise InspectionError('Ambiguous volume listing')
                volume = self.xml('vol-dumpxml', columns[0], '--pool', pool)
                path = self.path(volume.findtext('./target/path'))
                volumes.add(path)
                for backing in volume.findall('.//backingStore/path'):
                    references.add(self.path(backing.text))
        for source in sources - volumes:
            references.update(self.external_chain(source))
        return (candidates & volumes) - references - sources

    def cleanup(self, candidates, inspect_domains=False):
        removed = []
        for candidate in sorted(candidates):
            # Refresh the dependency view before each deletion.
            if candidate in self.unused({candidate}, inspect_domains=inspect_domains):
                self.output('vol-delete', candidate)
                removed.append(candidate)
        return removed
