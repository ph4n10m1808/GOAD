# exchange

!!! success "Thanks!"
    Credits and huge thanks to [aleemladha](https://github.com/aleemladha) for his exchange role and his help to test the extension.

- Extension name : `exchange`
- Compatibility  : GOAD, GOAD-Light, GOAD-Mini
- Providers : virtualbox/libvirt/azure/vmware/aws/ludus/proxmox
- Add a machine  : srv01 (the-eyrie.sevenkingdoms.local)  (ip_range.21)

!!! warning "resources"
    The GOAD-Light/libvirt override uses four vCPUs and10GiB RAM; other provider templates retain their own allocation. Allow memory and disk headroom for the existing lab and SOC; this allocation is not a performance guarantee.

!!! warning "impacts"
    Modify the ad schema and add a computer (warning the exchange machine is really heavy)

## Prerequisites

- GOAD or GOAD-Light installation

## Installation

- select your instance
```
load <instance_id>
```

- install the exchange extension
```
install_extension exchange
```

## Local libvirt media and lifecycle

The controller reuses its complete Exchange2019 CU9 ISO cache, downloading only when absent. Only Exchange receives a read-only CD-ROM; Windows discovers Setup by version instead of assuming a drive letter. Keep media through prerequisite reboots; verified Mailbox/Transport readiness permits ejection. UUID/name/ownership guards and completion markers protect retries; installation is not proved by a mounted ISO. The maintained source contract is `extensions/exchange/README.md` in the checkout.

`uninstall_extension exchange` supports the selected GOAD-Light/libvirt VM only. It keeps AD schema/configuration and shared ISO; it is not an Exchange application uninstall. Other provider removals need a separate plan. Sysmon and Elastic Agent remain explicit extensions, not implicit Exchange side effects.
