# Exchange extension

Adds Exchange (`the-eyrie`) and extends Active Directory for an Exchange lab. Installation changes the AD schema; deleting a VM does not roll that back. Current bundled media is historical Exchange 2019 CU9, not a newest-release or production recommendation.

## GOAD-Light on libvirt

In GOAD-Light/libvirt, upstream VM labels are retained: base IIS/MSSQL is SRV02 and Exchange SRV01. Upstream Ansible keys remain `srv02` for `castelblack` and `srv01` for `the-eyrie`; names, IPs and AD identities are not swapped. Other providers/labs retain their existing labels. GOAD-Light Exchange uses four vCPUs and 10 × 1024 MiB (10 GiB); other lab/provider memory settings are unchanged. This reduced lab allocation is not a measured performance guarantee. Its AD address is `<lab-prefix>.21`; an additional SOC NIC uses `192.168.50.23/24`, MAC `52:54:00:50:20:23`, network `soc-dedicated-es-private`. Reserve these values before creating it. Other lab/provider templates are unchanged. The shared libvirt template configures SOC after the normal VirtIO transition; do not configure a bootstrap NIC as if the transition already completed. Shared DNS/timezone/member-server roles apply, but verify actual time sync and DNS after installation.

GOAD-Light IIS/MSSQL SRV02 keeps Windows Update available during feature installation. Its inventory selects `no_update_after_install`, which invokes the native disable role in the final Windows provisioning play after successful lab configuration. The role verifies `wuauserv` stopped/disabled and `NoAutoUpdate=1`; failures stop provisioning. This is configuration only until the operator runs installation.

## Sysmon and native Elastic Agent

Both provisioning-only extensions target the Windows `domain` inventory group. Once Exchange is enabled, its inventory adds SRV01 to that group; no separate Agent VM or custom binary is needed. These are separate opt-in actions, not part of Exchange installation. Exchange provisioning never installs either implicitly. The 17 November scoped acceptance later recorded four Windows Agents/Sysmon with policy75 ACK and fresh logs; that is not complete channel/payload acceptance. For a separately authorized installation or rebuild, use the existing console commands:

```text
install_extension sysmon
install_extension elastic_agent
```

If already enabled, use `provision_extension sysmon` and `provision_extension elastic_agent` to reuse saved configuration. These commands cover all eligible domain hosts, not only SRV01; roles preserve healthy existing installations. Use the saved Windows Fleet policy/token and CA, not the Zeek token. Never put a real token into a shell example. Confirm Fleet ACK and fresh System/Security/Application, Sysmon, PowerShell and IIS data; Exchange-specific message tracking/HTTPProxy inputs require separate verified configuration. Do not treat DC-only/MSSQL settings as Exchange telemetry.

## Remove the local Exchange VM

```text
uninstall_extension exchange
```

CD-ROM attach/detach validates the saved Vagrant UUID and either the exact short domain name or this workspace’s exact pre-migration name. It never accepts an arbitrary SRV01 suffix from another instance. Runtime attach was rechecked after the 16 November rename: both live/saved read-only media definitions and the cached ISO stayed unchanged; guest Setup completion is a separate check.

Provider removal currently supports GOAD-Light/libvirt only. It validates the loaded instance's Vagrant UUID/name, gracefully halts only the Exchange SRV01 and calls the scoped native destroy path. Vagrant may request its normal destruction confirmation. Extension state is disabled only after VM absence is verified; other lab VMs and Sysmon/Agent extensions are retained. Full GOAD has its own base server names, so this shortcut refuses that lab and all other providers. Failed or uncertain removal keeps Exchange enabled for recovery.

This is VM removal, not an Exchange application uninstall or AD cleanup. Existing Exchange configuration, computer/account objects, DNS records and schema remain in AD; any cleanup or reinstallation recovery is a separate task. Source definitions and saved Fleet credentials remain available for a future rebuild.

## Installation reliability and log collection

The local Exchange 2019 role verifies a configured Mailbox role instead of treating service presence as success. Prerequisites include the official VC++ 2012/2013 packages; Setup native exit codes are checked, all GOAD forest domains are prepared and a native reboot follows successful installation.

The prerequisite role prepares its Windows working directory independently of ISO delivery. This is required for libvirt CD-ROM installation because no guest ISO-copy task runs. The completion marker is created with native `win_file` only after all prerequisite tasks, the final reboot and connection wait succeed; an existing completion marker remains untouched. After a prerequisite failure, reload the same instance and use `provision_extension exchange`; do not start directly at the marker or create it manually. This retries provisioning, not VM creation.

VC++ 2012/2013 use native `win_package.wait_for_children: true`: their bootstrapper can finish before an elevated child releases the downloaded executable. This prevents temporary-file cleanup racing that child. Keep native installed-product checks and reboot handling; do not ignore cleanup failures, change broad ACLs or create a prerequisite marker manually. On 16 November SRV01 already reported VC++ 2012 Update 4 and installer exit 0x0; the formerly locked file was subsequently readable with the saved user’s Full Control ACL. The next native provisioning run remains the recurrence check.

On libvirt, provider preflight uses the host cache at `extensions/exchange/ansible/iso/resources/iso/ExchangeServer2019-x64-CU9.ISO`; `GOAD_EXCHANGE_ISO` may select another absolute path. An existing ISO is retained; a missing one is downloaded over verified HTTPS to a temporary file and atomically published after a complete-length check. ISO and partial files are git-ignored; no ISO is tracked. An existing implausibly small file fails without overwrite. This is not a vendor-signature/hash authenticity claim.

The Vagrant definition adds read-only CD-ROM `hdd` only to Exchange; `hdc` remains available for VirtIO bootstrap. Windows discovers exactly one Exchange 2019 Setup.exe by CD-ROM and version, after prerequisite reboots, without assuming D:. No guest ISO transfer, Mount-DiskImage or file deletion runs on this libvirt path. Other providers retain the cache-copy path. Media remains available through setup/reboots. Only verified Mailbox configuration and a running transport service permit ejection from both live and saved definitions; a completion marker prevents later Vagrant reload from reattaching it. Failure keeps media for recovery. The shared ISO is retained when destroying Exchange.

System QEMU must be able to read the file and traverse its parents. Preflight opens the cache as the controller and preserves permissions/ACLs; native libvirt checks the actual QEMU identity and AppArmor/SELinux at launch. Named ACLs can grant traversal even when other-execute is absent, so those mode bits are not a valid access test. Diagnose exact-path access, never recursively change ownership. No guest start/media change is part of preparation. A running VM without the IDE CD-ROM requires regenerated configuration and an operator-controlled reload; live IDE hotplug is refused. Guest discovery, setup/reboots and ejection require separate runtime acceptance.

The legacy mail-reader bot is opt-in through `exchange_mail_reader_bot_enabled` (default false); it is not required for monitoring. Service accounts and built-in users are excluded from bulk lab mailbox creation. Use the native Exchange integration and one Windows Agent for file/channel collection; real guest paths, channel discovery and SMTP/message-flow verification are required after Setup.
