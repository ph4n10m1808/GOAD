# Libvirt / KVM

<a id="vm-names--16-november-2026"></a>

## VM names — 16 November 2026

All libvirt labs and VM extensions use exactly one lab prefix plus the existing machine role: `GOAD-Light-DC01`, `GOAD-Light-DC02`, `GOAD-Light-SRV02`, and optional Exchange `GOAD-Light-SRV01`. No instance hash or second lab prefix is added. Bare keys are also prefixed once (`MINILAB-DC01`, `MINILAB-WS01`, `GOAD-ELK`); existing specialized SCCM roles and Linux/WS/extension labels are retained. Logical Vagrant machine keys stay unchanged. Only one same-name lab can coexist on a libvirt connection; native duplicate-name checks refuse a collision. Windows hostnames, AD identities, UUIDs and disk paths are unchanged.

Existing running domains keep their old names until a maintenance window: libvirt `domrename` requires an inactive domain. Stop provisioning cleanly before renaming and keep the Vagrant UUID files unchanged. Never rebuild or rename disk files merely to shorten the VM label. Exchange removal recognizes only the loaded instance’s exact old/new name with its saved UUID and owned disk paths. The production XML define/restore path checks the saved UUID and retains the domain’s current name, so an older VirtIO backup cannot restore an obsolete label. On 16 November, the four existing GOAD-Light domains were renamed while already shut off; native Vagrant status still resolves their unchanged UUIDs.

## GOAD-Light lab additions (15 November 2026)

Current GOAD-Light source keeps upstream VM labels (SRV02 IIS/MSSQL, optional SRV01 Exchange) and only the existing DC01 CA. Legacy LAPS manages SRV02 RID-500, with north OU/GPO on DC02 and forest schema preparation on DC01. The separate Vagrant account remains usable; the selected lab reader is NORTH\samwell.tarly and native expiration-protection removal remains intentional.

Selected ESC2/3/4/6/7/9/10/11/13/15 conditions are prepared without uninstalling OS fixes. ESC3 includes both agent and target templates; ESC4 grants SEVENKINGDOMS\petyer.baelish control over only ESC4. NORTH gmsaDragon is available only to castelblack and has the selected lab GenericAll path to hodor; no IIS/SQL service identity is changed. DC01 hosts CA/template conditions; both DCs get native ESC10 KDC settings and DCs/SRV02 get Schannel UPN mapping. ESC13 links an empty Universal Security group to an issuance policy; that group has local Administrator rights only on SRV02. Patch-dependent ESC10/15 exploitability requires actual build/readback/testing. Existing ESC1 remains.

The final Windows provisioning play enables local Windows/CA/template/LAPS/gMSA audit generation and then disables SRV02 Windows Update, after server features have installed. Auditing preserves weak permissions and flags. No Sysmon/Elastic Agent installation is implied by base provisioning; both are separate opt-in extensions. The operator starts installation manually; source validation is not runtime or exploit acceptance. The SOC repository keeps the complete inventory at `docs/runbooks/goad-light-monitoring-log-inventory.txt`.

### Historical DC and manual install

DC02 now uses publisher Windows Server 2019 box `2020.07.17`; DC01/SRV02/Exchange retain `2021.05.15`. The historical DC is selected for Zerologon, EFSRPC coercion and noPac lab prerequisites. The final install verifies DC role, build 17763 with revision below 1397, Netlogon/KDC, positive machine-account quota and disabled updates; a patched/wrong image fails instead of uninstalling fixes. DC01 Web Enrollment `/certsrv` deliberately accepts NTLM over its existing HTTP binding without EPA/SSL enforcement. These are isolated lab conditions, not exploit results or a production baseline. Zerologon/PetitPotam/noPac are not exclusive to Server 2016.

Prepare the missing native box conversion before install. The previous goad.sh-only command is insufficient when this older libvirt box is absent. From this checkout (`<REPO_ROOT>/lab/goad`), run the following single shell command and wait:

```bash
env PATH="/opt/miniconda3/bin:$PATH" GOAD_CONDA_ENV=goad GOAD_VAGRANT_HOME=/mnt/SSD_DATA/.vagrant.d bash -c 'cd "<REPO_ROOT>/lab/goad" && ./scripts/prepare_libvirt_boxes.sh --lab GOAD-Light --extension exchange && printf "y\n" | ./goad.sh -t install -l GOAD-Light -p libvirt -m local -ip 192.168.49 -e exchange'
```

For an existing lab, keep the box-preparation step and add `-i INSTANCE_ID` to `goad.sh`. Use the full workspace folder ID from `workspace/<INSTANCE_ID>/instance.json`, not only its six-character prefix. `-t install -i INSTANCE_ID` reuses the saved lab, provider, addresses and extensions, then runs providing/base provisioning and the saved extensions; it does not create another instance. Example after preparing boxes:

```bash
./goad.sh -t install -i INSTANCE_ID -l GOAD-Light -p libvirt -m local
```

For only the failed gMSA play, append `-r ad-gmsa.yml`; that runs only this play and does not continue the full Exchange installation. Both forms are manual operator commands, not actions performed by this source review.

This operator-run command downloads/converts missing publisher boxes and installs the lab; it is not run by the source review. Keep both selected image versions, configured storage pool and manual-install-only intent. Sysmon and Elastic Agent require separate explicit installation; the 17 November host acceptance is recorded in the SOC backlog, not implied by this source procedure.

The libvirt provider runs GOAD locally with QEMU/KVM through `vagrant-libvirt`. GOAD's upstream Windows boxes are not published for libvirt, so they must first be converted to QCOW2 with the included helper.

## VM names in virt-manager

Current names use one lab prefix and machine role as defined [above](#vm-names--16-november-2026). The old instance-hash convention is retired. Keep UUIDs, Vagrant keys and disks unchanged; duplicate same-lab names cannot coexist on one libvirt connection.

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

All local labs, including GOAD-Light and its Exchange VM, use pool `GOAD` at `/mnt/SSD_DATA/Virtualization/KVM/GOAD`. The earlier separate GOAD-Light pool on SSD_DATA_1 is retired by the 15 November operator decision. Swap remains a separate host setting; it does not choose VM storage.

The shared Vagrant cache remains `/mnt/SSD_DATA/.vagrant.d`. Require `/mnt/SSD_DATA` to be mounted before preparing boxes or starting guests; fail closed on a wrong pool path. Existing disks are never moved implicitly. For the selected full rebuild, delete only the verified old instance VM volumes before recreating it in the shared pool.

```bash
pkexec install -d /mnt/SSD_DATA/Virtualization/KVM/GOAD
virsh -c qemu:///system pool-define-as GOAD dir --target /mnt/SSD_DATA/Virtualization/KVM/GOAD
virsh -c qemu:///system pool-start GOAD
virsh -c qemu:///system pool-autostart GOAD
```

Inspect an existing pool before running creation commands. The obsolete split-pool migration helper has been removed; relocating existing disks requires a separately reviewed migration plan.

The pool target is validated before starting Vagrant; an incorrect target stops the operation. To relocate the storage pool on another host, set `GOAD_STORAGE_POOL`, `GOAD_STORAGE_POOL_PATH`, `GOAD_STORAGE_MOUNT_ROOT` (empty string disables the mountpoint check when using a custom directory), and `GOAD_VAGRANT_HOME` consistently across `prepare_libvirt_boxes.sh`, `goad.sh`/`goad_docker.sh`, and `Vagrantfile`. Existing VM disks are not automatically migrated.

## Network isolation and lab credential boundary

- **SOC bridge isolation (`soc-dedicated-es-private` / `virbr50`):** GOAD-Light guests (`192.168.50.20`–`192.168.50.23`) are intentionally vulnerable Active Directory targets attached to `virbr50` solely for SOC telemetry ingestion. Before exposing the SOC host on that bridge, review and load the host firewall template at `template/provider/libvirt/soc-bridge-nftables.conf` (`sudo nft -c -f ...` then `sudo nft -f ...`) so lab guests can reach only the required telemetry ports (for example Fleet `8220/tcp` and Elasticsearch `9200/tcp`) on `192.168.50.1` and cannot pivot to host management services, Docker bridges, or the LAN.
- **Inherited GOAD credentials and WinRM transport:** Default GOAD domain/local accounts (`vagrant`/`vagrant`, lab AD passwords) and `config.winrm.ssl_peer_verification = false` are intentional isolated-lab conventions for local Vagrant provisioning. Never bridge GOAD lab networks directly to untrusted networks or reuse lab credentials outside this isolated lab.

GOAD refreshes the selected lab pool before starting Vagrant. This removes stale libvirt metadata left behind when a failed upload deletes a volume file.

The `libvirt-qemu` user also needs traverse permission on every parent directory of the pool. If the mount point is private (`0700`), grant only the required traverse permission instead of opening the whole disk:

```bash
sudo setfacl -m u:libvirt-qemu:--x /mnt/SSD_DATA
sudo setfacl -m u:libvirt-qemu:--x /mnt/SSD_DATA_1
```

## Relocate an existing GOAD-Light workspace

Old date — the former relocation helper targeted a separate GOAD-Light pool on SSD_DATA_1. The selected source/target policy now uses the same GOAD pool, so that helper refuses the operation. Do not run old migration commands as a current relocation procedure. Existing private journals remain recovery evidence; any future move requires exact storage/UUID/reference verification and separate approval.

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
`soc-dedicated-es-private`.

This is a persistent host-owned network, also used by the SOC gateway. Create it once from the GOAD directory before installing the lab:

```bash
virsh -c qemu:///system net-define template/provider/libvirt/soc-network.xml
virsh -c qemu:///system net-autostart soc-dedicated-es-private
virsh -c qemu:///system net-start soc-dedicated-es-private
virsh -c qemu:///system net-info soc-dedicated-es-private
ip -4 addr show dev virbr50
```

Run define/start only if absent/inactive; do not redefine an existing network without checking its XML. The definition has no DHCP, DNS or NAT. VM start/reload/resume requires the persistent gateway definition; `libvirt__always_destroy: false` prevents vagrant-libvirt from claiming an existing SOC network for destroy. Do not remove this bridge during lab cleanup. This option does not protect a UUID already recorded by older Vagrantfiles: inspect legacy `.vagrant/machines/*/libvirt/created_networks` before destroying an old instance and remove only the verified SOC network UUID from those ownership files, preserving every other entry. Fresh instances use the corrected template.

Static guest addresses:

- `GOAD-Light-DC01`: `192.168.50.20/24`
- `GOAD-Light-DC02`: `192.168.50.21/24`
- `GOAD-Light-SRV02`: `192.168.50.22/24`
- Optional `GOAD-Light-SRV01` Exchange: `192.168.50.23/24`

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
