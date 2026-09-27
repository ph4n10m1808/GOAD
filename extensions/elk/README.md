# ELK extension

- Extension Name: elk
- Description: Add an ELK to the current lab
- Machine name : {{lab_name}}-ELK
- Compatible with labs : *

## prerequisites

On ludus prepare template :
```
ludus templates add -d ubuntu-22.04-x64-server
ludus templates build
```

## Install

Install the standalone Sysmon extension first when endpoint telemetry is required:

```
instance_id> install_extension sysmon
instance_id> install_extension elk
```

ELK installs Winlogbeat on the Windows domain machines and collects the `Microsoft-Windows-Sysmon/Operational` channel when Sysmon is present. Sysmon is intentionally managed by its own extension so ELK and other collectors such as Splunk Universal Forwarder can consume the same telemetry without replacing its configuration.

The Winlogbeat policy collects the same Windows Event Log channels as the Splunk Universal Forwarder extension, including Security, Directory Service, Kerberos, NTLM, SMB, WinRM, PowerShell, WMI, RDP, DNS Server Analytical, Firewall, Defender, and Sysmon. Channels that do not exist on a particular Windows role are skipped safely.

The extension also enables the Advanced Audit Policy subcategories needed for AD authentication, directory changes, account and group management, process creation, file shares, filtering-platform telemetry, and AD CS. It forces Advanced Audit Policy precedence, enables PowerShell Script Block, invocation, and Module Logging, audits incoming NTLM traffic, includes command-line data in process creation events, enables monitored event channels, increases important event-log sizes, enables the full CA audit filter on AD CS servers, and applies an idempotent domain-root SACL for DCSync Event ID 4662. Filebeat collects IIS access logs so the collected sources remain aligned with the Splunk Universal Forwarder extension. These behaviors can be overridden through the role defaults.

```
instance_id> install_extension elk
```

- machine: {{lab_name}}-ELK
- Winlogbeat agent on domain computer machines


## Uninstall

- Not implemented yet
