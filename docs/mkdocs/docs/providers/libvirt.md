# Libvirt / KVM

The libvirt provider runs GOAD locally with QEMU/KVM through `vagrant-libvirt`. GOAD's upstream Windows boxes are not published for libvirt, so they must first be converted to QCOW2 with the included helper.

## VM names in virt-manager

New guests use the prefix `<lab-name>-<instance-id>`, for example
`GOAD-753549_GOAD-DC01`. Sort by name in virt-manager to keep guests from
the same lab instance together, including extensions and the provisioning VM.
This is a naming convention, not a collapsible folder in the interface.
Vagrant machine names used by GOAD commands remain unchanged.
Existing guests are not renamed; the prefix takes effect when creating guests
from the updated provider template.

## Independent VM disks

After guest setup and the Windows VirtIO transition, `install` flattens each
guest's backing chain with `virsh blockpull --wait`. Each configured disk then
contains its own data; a normal single-disk lab VM needs one independent QCOW2
file (the filename can still end in `.img`). VM definitions and any UEFI NVRAM
remain separate from the disk image.

This takes additional time and disk space for each VM. Guests with existing
snapshots are rejected for conversion. VM creation fails if flattening cannot be
verified; no base image is deleted in that case. Existing VMs are not converted
merely by `start`.

After all disks are independent, unused bases from this instance are removed
when inspection succeeds. Shared bases, saved guest state, snapshots, unmanaged
guest disks that cannot be inspected, or inaccessible pools can prevent cleanup. Cached source boxes in
Vagrant's box directory remain available for future VM creation. The original
base paths are saved in the workspace for subsequent cleanup during `destroy`.

## IOThread cho đĩa NVMe — 09/10/2026

Source cấu hình nằm trong `goad/provider/vagrant/libvirt.py`: provider thêm một IOThread cho đĩa boot VirtIO, với số hàng đợi bằng `min(2, vCPU)`; giữ `cache=none`, `io=native`, `discard=unmap` và `detect_zeroes=unmap`. Đây là cấu hình để thử giảm tranh chấp event loop QEMU, chưa phải số liệu chứng minh tăng tốc. `vagrant-libvirt 0.12.2` bỏ qua `iothread`/`queues` trong `disk_driver`, nên không đặt các key không hỗ trợ vào Vagrantfile.

GOAD `start`/`start_vm` kiểm tra và áp dụng cho VM hiện hữu đang tắt trước khi gọi Vagrant. VM đang chạy, managed-save và đĩa IDE bootstrap được giữ nguyên; mapping IOThread tùy chỉnh cũng được giữ. Windows mới nhận cấu hình khi chuyển boot disk sang VirtIO sau kiểm tra driver; Linux mới nhận khi start lại sau lần tạo đầu. Gọi Vagrant trực tiếp không chạy bước bổ sung này; dùng lifecycle GOAD.

Đã áp dụng offline cho 5 VM GOAD và 3 VM GOAD-Light ở hai workspace hiện hữu, mỗi VM một IOThread và hai queue. XML schema/UUID/đường dẫn disk đã kiểm tra; 53 test libvirt/storage đạt, gồm idempotence, bảo toàn identity, bỏ qua running/managed-save/IDE và chặn start khi tuning thất bại. Chưa boot Windows, kiểm tra AD/DNS/WinRM hoặc benchmark trước/sau; thay đổi có hiệu lực vào lần boot tiếp theo. Không đổi pool, disk image, network, tài nguyên CPU/RAM hay VM SOC.

XML trước thay đổi nằm tại `workspace/<instance>/provider/.goad/virtio/<machine>/pre-iothread-domain.xml` (0600, giữ bản đầu). Để hoàn tác, dừng VM qua GOAD, phục hồi XML đó bằng cơ chế `_define_domain_xml` của provider rồi bỏ hoặc điều chỉnh bước `tune_storage` trong source trước lần start tiếp theo; nếu giữ source hiện tại, GOAD sẽ tự áp lại cấu hình. Không restore XML cũ nếu disk/network đã được thay đổi sau thời điểm backup mà chưa đối chiếu.

## Cleanup on destroy

`destroy`, `destroy_vm`, and `delete` attempt to remove unused base images
and leftover `.img` / `.qcow2` volumes owned by the destroyed guests, including
Vagrant-named auxiliary disks such as `<domain>-vdb.qcow2`. Disk paths are saved
in `.goad/disk-cleanup.json` before destruction so cleanup can be retried while
the workspace remains available. Arbitrary shared disks and cached Vagrant
source boxes are not collected.

Other domains no longer block cleanup merely by existing: their live and
persistent disk references are checked alongside all active file pools and
backing chains. Local guest disks outside pools are inspected with `qemu-img
info --backing-chain`. If direct access fails (for example, root-owned disks),
GOAD reads volume metadata through libvirt using a temporary directory pool.
The pool is removed immediately after inspection; no pool is built, no disk
permissions are changed, and no external disk is modified. If both inspection
methods fail, images are retained with a warning. Saved guest state, snapshots, inactive or unsupported
pools, and inspection failures also prevent deletion. The dependency view is
refreshed before each deletion. Cleanup does not scan unrelated directories or
globally sweep images from old workspaces. Avoid concurrent VM creation or disk
changes during cleanup. A cleanup failure does not prevent workspace removal
after successful VM destruction.

## Requirements

- Linux with hardware virtualization enabled
- QEMU/KVM and a working `qemu:///system` libvirt connection
- Vagrant plugins `vagrant-libvirt` and `vagrant-reload`
- At least 120 GB free in the selected libvirt storage pool
- 20 GB RAM for GOAD-Light or 24 GB for GOAD

On Debian-family systems, install the host components with:

```bash
sudo apt install qemu-system-x86 libvirt-daemon-system libvirt-clients \
  libvirt-dev virtinst ebtables libguestfs-tools
vagrant plugin install vagrant-libvirt vagrant-reload
sudo usermod -aG kvm,libvirt "$USER"
```

Log out and back in after changing groups. Verify the host before continuing:

```bash
virsh -c qemu:///system list --all
```

## Storage

Keep the Vagrant cache and VM volumes on a filesystem with enough space:

```bash
export GOAD_VAGRANT_HOME=/mnt/SSD_DATA/.vagrant.d
export VAGRANT_HOME="$GOAD_VAGRANT_HOME"
```

The selected lab fixes the VM and snapshot storage pool:

| Lab | Pool | VM image directory |
| --- | --- | --- |
| GOAD-Light | `GOAD-Light` | `/mnt/SSD_DATA_1/Virtualization/KVM/GOAD-Light` |
| Other labs | `GOAD` | `/mnt/SSD_DATA/Virtualization/KVM/GOAD` |

The shared Vagrant box cache stays in `/mnt/SSD_DATA/.vagrant.d` and the VirtIO
ISO stays on the first SSD. GOAD-Light extensions and a provisioning VM inherit
the GOAD-Light pool. No environment override can fall back to the former pool.
The second SSD must be mounted before preparing boxes, creating, reloading or
resuming GOAD-Light guests; an unavailable mount or incorrect pool target stops
the operation.

Create the pool once if it does not exist, then start it and enable autostart:

```bash
# GOAD-Light
sudo mkdir -p /mnt/SSD_DATA_1/Virtualization/KVM/GOAD-Light
virsh -c qemu:///system pool-define-as GOAD-Light dir --target /mnt/SSD_DATA_1/Virtualization/KVM/GOAD-Light
virsh -c qemu:///system pool-start GOAD-Light
virsh -c qemu:///system pool-autostart GOAD-Light

# Other labs, when needed
sudo mkdir -p /mnt/SSD_DATA/Virtualization/KVM/GOAD
virsh -c qemu:///system pool-define-as GOAD dir --target /mnt/SSD_DATA/Virtualization/KVM/GOAD
virsh -c qemu:///system pool-start GOAD
virsh -c qemu:///system pool-autostart GOAD
```

The pool target is validated before starting Vagrant; an incorrect target stops the operation. GOAD_LIBVIRT_STORAGE_POOL no longer overrides this fixed location. Existing VM disks are not automatically migrated.

GOAD refreshes the selected lab pool before starting Vagrant. This removes stale libvirt metadata left behind when a failed upload deletes a volume file.

The `libvirt-qemu` user also needs traverse permission on every parent directory of the pool. If the mount point is private (`0700`), grant only the required traverse permission instead of opening the whole disk:

```bash
sudo setfacl -m u:libvirt-qemu:--x /mnt/SSD_DATA
sudo setfacl -m u:libvirt-qemu:--x /mnt/SSD_DATA_1
```

## Relocate an existing GOAD-Light workspace

The offline helper only supports independent single-disk QCOW2 guests without
snapshots, managed save or UEFI NVRAM. Shut off the lab through GOAD first and
avoid concurrent VM/disk changes. Run from the GOAD checkout:

```bash
python3 scripts/migrate_goad_light_storage.py workspace/<id>-goad-light-libvirt
python3 scripts/migrate_goad_light_storage.py workspace/<id>-goad-light-libvirt --apply
```

The first command previews the exact domains and required space. Apply downloads
sparse image contents through libvirt, checks QCOW2 integrity and full-file SHA256,
then changes only persistent disk paths while keeping VM UUIDs and settings.
It updates the existing workspace Vagrantfile without regenerating its settings.
Old images are removed only after verifying definitions and checking libvirt
references; guests remain shut off. Definition backups and aggregate evidence
are stored in a private `.migration-<workspace>` directory beside the new images.
After an interruption, inspect this journal and the actual domain paths before
manual recovery; the helper deliberately refuses to overwrite existing targets.
Source volumes remain available until the final cleanup phase.

## Prepare provider-specific boxes

Prepare only the boxes required by the selected lab and extensions:

```bash
./scripts/prepare_libvirt_boxes.sh --lab GOAD-Light
```

Preview the download and conversion commands without changing the box cache:

```bash
./scripts/prepare_libvirt_boxes.sh --lab GOAD-Light --dry-run
```

An example with extensions:

```bash
./scripts/prepare_libvirt_boxes.sh --lab GOAD-Light \
  --extension exchange --extension guacamole
```

The included converter reads the cached VirtualBox OVF, preserves its disk controller, converts the VMDK to QCOW2 with `qemu-img`, and adds a local `libvirt` provider with the same box name and version. It can require tens of GB of temporary and final storage.

## Check and install

```bash
./goad.sh -t check -l GOAD-Light -p libvirt -ip 192.168.56
./goad.sh -t install -l GOAD-Light -p libvirt -ip 192.168.56
```

The generated Vagrantfile uses the configured libvirt storage pool, host CPU passthrough, and an isolated private network. Linux guests use VirtIO immediately. Windows guests keep their boot disk on IDE, install the VirtIO drivers automatically, and then transition the private and management NICs to VirtIO with health checks and rollback. Keeping the converted Windows boot disk on IDE avoids boot failures caused by older images that do not register `viostor` as a boot-critical driver.

### Private gateway and SOC network

Each Windows guest keeps its management/NAT NIC as the preferred default route.
The libvirt private NIC also receives a persistent, high-metric default gateway
at `<GOAD subnet>.1`; this provides a fallback without stealing normal Internet
traffic from the NAT NIC. A more-specific persistent route for
`GOAD_SOC_NETWORK` (default `192.168.50.0/24`) uses that private gateway so
GOAD can reach the SOC private subnet. Override `GOAD_SOC_NETWORK` when the SOC
private subnet differs.

GOAD-Light is handled differently: every domain VM always has a dedicated
third NIC attached to the existing libvirt network
`soc-dedicated-es-private`. The static addresses are:

- `GOAD-Light-DC01`: `192.168.50.20/24`
- `GOAD-Light-DC02`: `192.168.50.21/24`
- `GOAD-Light-SRV02`: `192.168.50.22/24`

The NIC is present from the first VM definition. Windows configures its static
address after the VirtIO network transition, and verifies it again on later
`vagrant up` runs. It receives the persistent gateway `192.168.50.1` with metric
`2000`, so every NIC has a gateway while NAT remains preferred and the lab NIC
remains the first fallback. DNS registration is disabled on the SOC NIC so its
monitoring address is not published in AD DNS. GOAD-Light therefore does not
install the legacy SOC route through its AD private gateway. Because the shared
SOC addresses and MACs are fixed, run only one GOAD-Light instance on this SOC
network at a time.

For other labs, the route does not itself enable host forwarding. The host must allow the
required traffic between the GOAD and SOC libvirt bridges, and the SOC guest
must have a return path. Keep the firewall scope limited to the GOAD source
subnet, SOC destination, and required service ports; do not expose the
vulnerable lab to public or untrusted networks. The private gateway configuration
is provisioned on every Vagrant up, so new libvirt Windows guests receive it too.

The default VirtIO ISO path is:

```bash
/mnt/SSD_DATA/Virtualization/KVM/virtio-win-0.1.271.iso
```

Override both the ISO and its pinned checksum when using another build:

```bash
export GOAD_VIRTIO_WIN_ISO=/path/to/virtio-win.iso
export GOAD_VIRTIO_WIN_ISO_SHA256=<sha256>
```

Per-VM transition state and recovery XML are stored below the generated provider directory in `.goad/virtio/`. Do not remove these files while a transition is running.
Successful `destroy` and `destroy_vm` operations remove the corresponding transition state so the next build always starts with IDE/e1000 and reinstalls the guest drivers.

Do not bridge this intentionally vulnerable lab to an untrusted or public network.

## Troubleshooting

Confirm that every pinned box has a libvirt variant in the active `VAGRANT_HOME`:

```bash
vagrant box list
```

Inspect provider state with:

```bash
virsh -c qemu:///system list --all
virsh -c qemu:///system net-list --all
virsh -c qemu:///system pool-info GOAD
```


### Startup timeouts

Libvirt guests allow up to 1200 seconds for boot readiness and 600 seconds
for an individual WinRM readiness check, with 60 connection attempts and a
10-second retry delay. Startup continues as soon as the guest responds.
Restart the failed GOAD command to load updated settings; an already running
Vagrant process keeps its previous timeout settings.
