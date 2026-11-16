"""Host-side Exchange CD-ROM lifecycle; never starts or stops a guest."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

ISO_NAME = 'ExchangeServer2019-x64-CU9.ISO'
ISO_URL = 'https://download.microsoft.com/download/d/7/b/d7bcf78a-00d2-4a46-a3d2-7d506116bcd2/' + ISO_NAME
PROJECT = Path(__file__).resolve().parents[3]


def media_path():
    return Path(os.environ.get('GOAD_EXCHANGE_ISO', str(PROJECT / 'extensions/exchange/ansible/iso/resources/iso' / ISO_NAME))).resolve()


def completed(workspace, vm_name):
    workspace = Path(workspace)
    identifier = workspace / '.vagrant/machines' / vm_name / 'libvirt/id'
    marker = workspace / '.goad/exchange-media' / (vm_name + '.complete')
    if not identifier.is_file() or not marker.is_file():
        return False
    import uuid
    try:
        return str(uuid.UUID(identifier.read_text().strip())) == str(uuid.UUID(marker.read_text().strip()))
    except ValueError:
        return False


def prepare():
    path = media_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / '.exchange-media.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare_locked(path)


def _prepare_locked(path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as output:
                tmp = Path(output.name)
                with urllib.request.urlopen(ISO_URL, timeout=120) as response:
                    expected = int(response.headers.get('Content-Length', '0'))
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        total += len(chunk)
                if not total or (expected and total != expected):
                    raise RuntimeError('Incomplete Exchange ISO download')
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o644)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    if not path.is_file() or path.stat().st_size < 1024 * 1024 * 1024:
        raise RuntimeError('Exchange cache is not a complete-size ISO; inspect it without overwriting')
    # Other-mode bits cannot establish QEMU access: a named ACL can grant
    # traversal on a private mount. Check the controller's actual read path;
    # native libvirt enforces QEMU credentials and AppArmor/SELinux at launch.
    # Never change cache/parent permissions or ACLs here.
    try:
        with path.open('rb') as source:
            source.read(1)
    except PermissionError:
        raise RuntimeError(f'Exchange ISO is unreadable by the controller: {path}; review exact file/parent access, no permissions changed') from None
    return path


def virsh(*args):
    result = subprocess.run(['virsh', '-c', 'qemu:///system', *args], text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Exchange media virsh operation failed: ' + result.stderr.strip())
    return result.stdout


def change(workspace, action, vm_name):
    workspace = Path(workspace).resolve()
    if not vm_name.endswith('-SRV01') or '/' in vm_name:
        raise RuntimeError('Exchange media is limited to the explicit SRV01 VM')
    domain = (workspace / '.vagrant/machines' / vm_name / 'libvirt/id').read_text().strip()
    import uuid
    domain = str(uuid.UUID(domain))
    root = ET.fromstring(virsh('dumpxml', domain, '--inactive'))
    running = virsh('domstate', domain).strip() != 'shut off'
    lab = vm_name.removesuffix('-SRV01')
    instance = workspace.parent.name.split('-')[0]
    # Short names are canonical; accept the exact pre-migration name only for
    # this selected instance. Never accept an arbitrary suffix match.
    owned_names = {vm_name, lab + '-' + instance + vm_name}
    if root.findtext('uuid') != domain or root.findtext('name') not in owned_names:
        raise RuntimeError('Exchange domain identity differs')
    path = media_path()
    selected = None
    for disk in root.findall('./devices/disk'):
        target = disk.find('target')
        if target is not None and target.get('dev') == 'hdd':
            selected = disk
    marker = workspace / '.goad/exchange-media' / (vm_name + '.complete')
    if action == 'attach':
        path = prepare()
        if completed(workspace, vm_name):
            raise RuntimeError('Exchange media already marked complete; explicitly review before reattachment')
        if selected is not None:
            source = selected.find('source')
            if (selected.get('device') != 'cdrom' or source is None or
                    source.get('file') != str(path) or selected.find('readonly') is None):
                raise RuntimeError('hdd is occupied by a different or writable device; refusing replacement')
        else:
            if running:
                raise RuntimeError('Exchange CD-ROM device hdd is absent; use the reviewed Vagrant definition on the next operator-controlled reload, not IDE hotplug')
            disk = ET.Element('disk', {'type': 'file', 'device': 'cdrom'})
            ET.SubElement(disk, 'driver', {'name': 'qemu', 'type': 'raw'})
            ET.SubElement(disk, 'source', {'file': str(path)})
            ET.SubElement(disk, 'target', {'dev': 'hdd', 'bus': 'ide'})
            ET.SubElement(disk, 'readonly')
            with tempfile.NamedTemporaryFile(mode='w', suffix='.xml') as tmp:
                tmp.write(ET.tostring(disk, encoding='unicode')); tmp.flush()
                # --persistent applies to live and saved config for a running VM.
                virsh('attach-device', domain, tmp.name, '--persistent')
        trees = [ET.fromstring(virsh('dumpxml', domain, '--inactive'))]
        if running:
            trees.append(ET.fromstring(virsh('dumpxml', domain)))
        for tree in trees:
            matches = [d for d in tree.findall('./devices/disk')
                       if d.get('device') == 'cdrom' and d.find('readonly') is not None
                       and d.find('target') is not None and d.find('target').get('dev') == 'hdd'
                       and d.find('source') is not None and d.find('source').get('file') == str(path)]
            if len(matches) != 1:
                raise RuntimeError('Expected exactly one read-only Exchange CD-ROM in live and saved definitions')
        return
    # Recover a partial eject without replacing unrelated media. Config/live
    # are inspected and ejected independently; an empty CD-ROM is already safe.
    for flag in ['--config'] + (['--live'] if running else []):
        tree = ET.fromstring(virsh('dumpxml', domain, *(['--inactive'] if flag == '--config' else [])))
        for disk in tree.findall('./devices/disk'):
            target = disk.find('target')
            if target is None or target.get('dev') != 'hdd':
                continue
            source = disk.find('source')
            filename = None if source is None else source.get('file')
            if disk.get('device') != 'cdrom' or filename not in (None, '', str(path)):
                raise RuntimeError('hdd is not the expected Exchange media; refusing eject')
            if filename == str(path):
                virsh('change-media', domain, 'hdd', '--eject', flag)
    trees = [ET.fromstring(virsh('dumpxml', domain, '--inactive'))]
    if running:
        trees.append(ET.fromstring(virsh('dumpxml', domain)))
    for tree in trees:
        for disk in tree.findall('./devices/disk'):
            source = disk.find('source')
            if source is not None and source.get('file') == str(path):
                raise RuntimeError('Exchange media remains attached; completion not recorded')
    marker.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=marker.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(domain + '\n')
        output.flush()
        os.fsync(output.fileno())
    try:
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'attach', 'detach'])
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--vm-name')
    args = parser.parse_args()
    if args.action == 'prepare':
        print(prepare())
    else:
        if not args.workspace or not args.vm_name:
            parser.error('attach/detach require workspace and vm-name')
        change(args.workspace, args.action, args.vm_name)
        print(json.dumps({'action': args.action, 'verified': True}))
