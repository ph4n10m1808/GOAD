# Road Map

## SOC source delivery — 17 November 2026

Grouped the reviewed local changes into native AD/logging prerequisites, libvirt/Exchange lifecycle, maintained documentation and offline test compatibility. The lab retains its selected vulnerable conditions; publishing source does not authorize another provisioning, restart or attack. gMSA/LAPS use explicit forest/domain readback, SOC accounts retain private ownership receipts, and Agent/Sysmon stay opt-in. Libvirt keeps external SOC-network ownership and exact VM/media identity; Exchange uses a verified controller ISO/read-only CD-ROM, guarded forest/SPN recovery and native file/logging setup.

Validation:185 offline tests passed; all16 base playbooks and three extension install playbooks passed native syntax checks with the shared/extension role paths. Python/YAML and all seven AD CS JSON templates parsed, preserving four native UTF-16 templates. Source review and earlier scoped guest evidence remain separate: no VM, Agent, Exchange Setup or lab data was changed for this delivery.


## Local documentation review — 17 November 2026

Reviewed all 80 non-vendor Markdown files in the lab tree, including inherited provider/developer pages and olddocs as historical material; two ADCSTemplate vendor READMEs and one certificate/flag payload were excluded. Corrected current GOAD/GOAD-Light identities, extension/CLI/media/naming rules and source-versus-runtime wording. Twenty old image links were rebased to their existing `docs/img` assets; no old asset, scenario or lab data was deleted. The inherited roadmap below is publisher history, not the SOC execution checklist. Current source procedures are [arguments](usage/goad_args.md), [console](usage/goad_console.md) and [libvirt](providers/libvirt.md); actual data acceptance belongs to the SOC backlog. External link availability, non-libvirt deployment and attack results were not tested by this documentation review.

## v1
- [X] SMB share anonymous
- [X] SMB not signed
- [X] Responder
- [X] Zerologon
- [X] Windows defender
- [X] ASREPRoast
- [X] Kerberoasting
- [X] AD Acl abuse 
- [X] Unconstraint delegation
- [X] Ntlm relay

## v2
- [X] Password reuse between computer (PTH)
- [X] Spray User = Password
- [X] Password in description
- [X] Constrained delegation
- [X] Install MSSQL
- [X] MSSQL trusted link
- [X] MSSQL impersonate
- [X] Install IIS
- [X] Upload asp app
- [X] Multiples forest
- [X] Anonymous RPC user listing
- [X] Child parent domain
- [X] Generate certificate and enable ldaps
- [X] ADCS - ESC 1/2/3/4/6/8
- [X] Certifry
- [X] Samaccountname/nopac
- [X] Petitpotam unauthent
- [X] Printerbug
- [X] Drop the mic
- [X] Shadow credentials
- [X] Mitm6
- [X] Add LAPS
- [X] GPO abuse
- [X] Add Webdav
- [X] Add RDP bot
- [X] Add full proxmox integration
- [X] Add Gmsa (receipe created)
- [X] Add azure support
- [X] Refactoring lab and providers
- [X] Protected Users
- [X] Account is sensitive
- [X] Add PPL
- [X] Add Gmsa
- [X] Groups inside groups
- [X] Shares with secrets (all, sysvol)
- [X] Sccm (see SCCM lab)

## v3
- [X] aws support
- [X] ludus support
- [X] windows install compatibility
- [X] extension support
- [X] multiple instance management
- [X] extension exchange
- [X] extension ludus
- [X] extension elk
- [X] extension ws01
- [X] extension exchange add a bot to read mails
- [X] extension guacamole
- [X] extension linux VM enrolled
- [X] ADCS add vulns

## maybe one day if i get the time
- [ ] new training lab
- [ ] extension "life", add some bots in the lab
- [ ] extension Xworkstations, to add multiple workstations
- [ ] extension attackbox
- [ ] extension VPN
- [ ] Add Applocker to ws01
- [ ] Wsus (to add on sccm)

