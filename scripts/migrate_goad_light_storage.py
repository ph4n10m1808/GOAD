#!/usr/bin/env python3
"""Offline, verified relocation of an existing GOAD-Light libvirt workspace.

No guest is started. Download through libvirt preserves root-owned source data;
source volumes are removed only after whole-file hashes and definitions match.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

# This helper lives in scripts/ in the project checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goad.provider.vagrant.storage import storage_pool_for_lab

VIRSH = ['virsh', '--quiet', '-c', 'qemu:///system']


def run(args):
    return subprocess.check_output(args, text=True, env=dict(os.environ, LC_ALL='C'))


def virsh(*args):
    return run(VIRSH + list(args))


def stopped(uuid):
    if virsh('domstate', uuid).strip() != 'shut off':
        raise RuntimeError(f'{uuid}: guest must be shut off')
    if virsh('snapshot-list', uuid, '--name').strip():
        raise RuntimeError(f'{uuid}: snapshots must be handled before relocation')
    if any(line.partition(':')[0].strip() == 'Managed save' and line.partition(':')[2].strip() == 'yes'
           for line in virsh('dominfo', uuid).splitlines()):
        raise RuntimeError(f'{uuid}: managed save must be handled before relocation')


def digest_file(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def digest_volume(pool, name):
    digest = hashlib.sha256()
    process = subprocess.Popen(VIRSH + ['vol-download', name, '/dev/stdout', '--pool', pool], stdout=subprocess.PIPE)
    try:
        while chunk := process.stdout.read(8 * 1024 * 1024):
            digest.update(chunk)
        if process.wait() != 0:
            raise RuntimeError(f'Cannot checksum source volume {name}')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    return digest.hexdigest()


def volume_names(pool):
    # --quiet suppresses table headers; vol-list has no --name option.
    names = []
    for row in virsh('vol-list', pool).splitlines():
        if not row.strip():
            continue
        columns = re.split(r'\s{2,}', row.strip())
        if len(columns) != 2 or not Path(columns[1]).is_absolute():
            raise RuntimeError('Ambiguous volume listing; preserving sources')
        names.append(columns[0])
    return names


def cleanup_sources(entries, source_pool, target_pool, journal, evidence):
    # Before deleting, ensure no domain references the old files, live or persistent.
    old_paths = {str(e['source']) for e in entries}
    for uuid in virsh('list','--all','--uuid').split():
        for extra in ([],['--inactive']):
            tree = ET.fromstring(virsh('dumpxml',uuid,*extra))
            if any(node.get('file') in old_paths for node in tree.findall('.//source')):
                raise RuntimeError('A domain still references an old disk; preserving all sources')
    for pool in virsh('pool-list','--all','--name').split():
        if 'running' not in virsh('pool-info',pool):
            raise RuntimeError('Inactive pool prevents dependency inspection')
        virsh('pool-refresh',pool)
        for volume in volume_names(pool):
            tree = ET.fromstring(virsh('vol-dumpxml',volume,'--pool',pool))
            if any(node.text in old_paths for node in tree.findall('.//backingStore/path')):
                raise RuntimeError('A volume still depends on an old disk; preserving sources')
    for e in entries:
        stopped(e['uuid'])
        virsh('vol-delete',e['source'].name,'--pool',source_pool)
    virsh('pool-refresh',source_pool)
    virsh('pool-refresh',target_pool)
    for e in entries:
        stopped(e['uuid'])
        if e['source'].exists() or not e['target'].exists():
            raise RuntimeError('Final volume location mismatch')
    (journal / 'result.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps({'complete':True,'domains':len(entries),'all_shut_off':True,
                      'source_removed':True,'evidence':str(journal / 'result.json')}),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path, help='GOAD-Light workspace directory')
    parser.add_argument('--apply', action='store_true', help='copy, verify, redefine and remove old volumes')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not workspace.name.endswith('-goad-light-libvirt'):
        raise RuntimeError('Expected a GOAD-Light libvirt workspace')
    provider = workspace / 'provider'
    source_pool, source_root = storage_pool_for_lab('GOAD')
    target_pool, target_root = storage_pool_for_lab('GOAD-Light')
    if not os.path.ismount('/mnt/SSD_DATA_1'):
        raise RuntimeError('/mnt/SSD_DATA_1 must be mounted')
    if ET.fromstring(virsh('pool-dumpxml', source_pool)).findtext('target/path') != str(source_root):
        raise RuntimeError('Unexpected source pool path')
    entries = []
    for id_file in sorted((provider / '.vagrant/machines').glob('*/libvirt/id')):
        uuid = id_file.read_text().strip()
        stopped(uuid)
        xml = virsh('dumpxml', uuid, '--inactive')
        tree = ET.fromstring(xml)
        name = tree.findtext('name')
        if not name.startswith('GOAD-Light-') or tree.findtext('uuid') != uuid:
            raise RuntimeError('Workspace/domain identity mismatch')
        if tree.find('os/nvram') is not None:
            raise RuntimeError('NVRAM relocation needs separate handling')
        disks = tree.findall('devices/disk[@device="disk"]')
        if len(disks) != 1:
            raise RuntimeError(f'{name}: expected one independent disk')
        disk = disks[0]
        if disk.find('source') is None or disk.find('source').get('file') != str(source_root / (name + '.img')):
            raise RuntimeError(f'{name}: unexpected source path; refusing a repeated/partial migration')
        source = source_root / (name + '.img')
        volume_xml = ET.fromstring(virsh('vol-dumpxml', source.name, '--pool', source_pool))
        if volume_xml.findtext('backingStore/path') or disk.find('backingStore/source') is not None:
            raise RuntimeError(f'{name}: disk must be independent')
        target = target_root / source.name
        if target.exists():
            raise RuntimeError(f'Target already exists: {target}')
        entries.append(dict(uuid=uuid, name=name, source=source, target=target, xml=xml))
    if not entries:
        raise RuntimeError('No workspace domains found')
    needed = sum(e['source'].stat().st_size for e in entries)
    if shutil.disk_usage('/mnt/SSD_DATA_1').free < needed + 5 * 1024 ** 3:
        raise RuntimeError('Insufficient target space including 5 GiB reserve')
    print(json.dumps({'domains':[e['name'] for e in entries], 'bytes_upper_bound':needed,
                      'target':str(target_root),'apply':args.apply}), flush=True)
    if not args.apply:
        return
    target_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    journal = target_root / ('.migration-' + workspace.name)
    journal.mkdir(mode=0o700)
    with (journal / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pools = virsh('pool-list', '--all', '--name').split()
        if target_pool not in pools:
            virsh('pool-define-as', target_pool, 'dir', '--target', str(target_root))
        if ET.fromstring(virsh('pool-dumpxml', target_pool)).findtext('target/path') != str(target_root):
            raise RuntimeError('Unexpected target pool path')
        if 'running' not in virsh('pool-info', target_pool):
            virsh('pool-start', target_pool)
        virsh('pool-autostart', target_pool)
        evidence = []
        for e in entries:
            stopped(e['uuid'])
            before = e['source'].stat()
            print('Copying ' + e['name'], flush=True)
            e['target'].touch(mode=0o600, exist_ok=False)
            virsh('vol-download', e['source'].name, str(e['target']), '--pool', source_pool, '--sparse')
            info = json.loads(run(['qemu-img','info','--output=json','--backing-chain',str(e['target'])]))
            if len(info) != 1 or info[0]['format'] != 'qcow2' or info[0].get('backing-filename'):
                raise RuntimeError('Copied image has unexpected backing chain or format')
            run(['qemu-img','check','--output=json',str(e['target'])])
            copied = digest_file(e['target'])
            original = digest_volume(source_pool, e['source'].name)
            stopped(e['uuid'])
            after = e['source'].stat()
            if copied != original or (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
                raise RuntimeError('Source changed or whole-file checksum mismatch')
            if e['target'].stat().st_size != before.st_size:
                raise RuntimeError('Image file size mismatch')
            backup = journal / (e['name'] + '.before.xml')
            backup.touch(mode=0o600)
            backup.write_text(e['xml'])
            tree = ET.fromstring(e['xml'])
            tree.find('devices/disk[@device="disk"]/source').set('file',str(e['target']))
            e['new_xml'] = ET.tostring(tree,encoding='unicode')
            changed = journal / (e['name'] + '.after.xml')
            changed.touch(mode=0o600)
            changed.write_text(e['new_xml'])
            e['new_xml_path'] = changed
            evidence.append({'name':e['name'],'uuid':e['uuid'],'source':str(e['source']),
                             'target':str(e['target']),'sha256_equal':True,'image_check':True,
                             'allocated_before':before.st_blocks*512,
                             'allocated_after':e['target'].stat().st_blocks*512})
            (journal / 'verified-images.json').write_text(json.dumps(evidence,indent=2))
            print('Verified ' + e['name'], flush=True)
        virsh('pool-refresh', target_pool)
        defined = []
        try:
            for e in entries:
                stopped(e['uuid'])
                if virsh('dumpxml', e['uuid'], '--inactive') != e['xml']:
                    raise RuntimeError('Domain definition changed during copy')
                virsh('define',str(e['new_xml_path']))
                defined.append(e)
                actual = ET.fromstring(virsh('dumpxml',e['uuid'],'--inactive'))
                actual.find('devices/disk[@device="disk"]/source').set('file',str(e['source']))
                if ET.tostring(actual) != ET.tostring(ET.fromstring(e['xml'])):
                    raise RuntimeError('Unexpected domain changes beyond source path')
        except Exception:
            for e in defined:
                virsh('define',str(journal / (e['name'] + '.before.xml')))
            raise
        # Update the persisted Vagrantfile without regenerating operator settings.
        vagrantfile = provider / 'Vagrantfile'
        old = vagrantfile.read_text()
        (journal / 'Vagrantfile.before').write_text(old)
        guard = (Path(__file__).resolve().parents[1] / 'template/provider/libvirt/Vagrantfile').read_text().split('Vagrant.configure',1)[0].replace('{{lab_name}}','GOAD-Light')
        updated = guard + 'Vagrant.configure' + old.split('Vagrant.configure',1)[1]
        updated = updated.replace("libvirt.storage_pool_name = 'GOAD'",'libvirt.storage_pool_name = goad_storage_pool')
        updated = updated.replace("libvirt.snapshot_pool_name = 'GOAD'",'libvirt.snapshot_pool_name = goad_storage_pool')
        vagrantfile.write_text(updated)
        run(['ruby','-c',str(vagrantfile)])
        # Retire stale tracking of absent bases on the former pool.
        state = provider / '.goad/base-images.json'
        if state.exists():
            payload = json.loads(state.read_text())
            if payload.get('owner') != str(provider):
                raise RuntimeError('Unexpected cleanup state owner')
            candidates = payload.get('candidates',[])
            if any(Path(p).parent != source_root or Path(p).exists() for p in candidates):
                raise RuntimeError('Existing tracked base needs a separate migration decision')
            (journal / 'base-images.before.json').write_text(state.read_text())
            payload['candidates'] = []
            state.write_text(json.dumps(payload))
        cleanup_sources(entries, source_pool, target_pool, journal, evidence)


if __name__ == '__main__':
    main()
