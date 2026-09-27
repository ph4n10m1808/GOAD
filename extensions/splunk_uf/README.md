# Splunk Universal Forwarder extension

This agent-only extension installs Splunk Universal Forwarder 10.4.1 on every Windows machine in the `domain` inventory group. It sends data directly to a Splunk receiver and does **not** configure or require a Deployment Server.

The telemetry baseline is adapted from:

```text
/mnt/SSD_DATA/Work/VNCS Global/VNCS_Work/Task3/GOAD/splunk_config/windows_uf
```

## Defaults

```text
Receiver:            192.168.58.10:9997
Windows index:       wineventlog
IIS index:           web
Windows service:     SplunkForwarder (LocalSystem)
Splunk local user:   splunk
Splunk local pass:   GOAD_SPLUNK_UF_PASSWORD (required environment variable)
Deployment Server:  disabled/not configured
```

The `splunk` account is an internal, local Splunk administrator account. It is not a Windows local user and is not a domain account. The Windows service runs as `LocalSystem` so it can read protected Windows Event Logs on domain controllers and member servers.

Set a lab-specific password in the controller/provisioning environment before installation:

```bash
export GOAD_SPLUNK_UF_PASSWORD='replace-with-a-strong-lab-password'
```

Do not reuse this password outside the lab. With a jumpbox or runner provisioner,
export it in that provisioning environment as well.

## Install

Install Sysmon first if Sysmon telemetry is required:

```text
install_extension sysmon
install_extension splunk_uf
```

The extension enables the Advanced Audit Policy subcategories needed for AD authentication, directory changes, account and group management, process creation, file shares, filtering-platform telemetry, and AD CS. It forces Advanced Audit Policy precedence, enables PowerShell Script Block, invocation, and Module Logging, audits incoming NTLM traffic, includes command-line data in process creation events, enables monitored event channels, increases important event-log sizes, enables the full CA audit filter on AD CS servers, and applies an idempotent domain-root SACL for DCSync Event ID 4662. Unsupported subcategories and unavailable channels are skipped safely, and these behaviors can be overridden through the role defaults.

To override the receiver, define variables in an inventory loaded by GOAD:

```ini
[all:vars]
splunk_uf_receiver_host=192.168.58.10
splunk_uf_receiver_port=9997
splunk_uf_windows_index=wineventlog
splunk_uf_web_index=web
```

By default, an unavailable receiver produces a warning but does not roll back the UF installation. Set `splunk_uf_require_receiver=true` to make connectivity failure fatal.

## Manual configuration and recovery

Run PowerShell as Administrator on a Windows endpoint:

```powershell
$SplunkHome = 'C:\Program Files\SplunkUniversalForwarder'
Set-Location "$SplunkHome\bin"

.\splunk.exe btool inputs list --debug
.\splunk.exe btool outputs list --debug
$Credential = 'splunk:<GOAD_SPLUNK_UF_PASSWORD value>'
.\splunk.exe list forward-server -auth $Credential
.\splunk.exe restart -auth $Credential
```

GOAD-managed files are located at:

```text
C:\Program Files\SplunkUniversalForwarder\etc\apps\goad_inputs\local\inputs.conf
C:\Program Files\SplunkUniversalForwarder\etc\apps\goad_inputs\local\outputs.conf
```

After editing either file manually, validate it with `btool` before restarting the service.

Connectivity test:

```powershell
Test-NetConnection 192.168.58.10 -Port 9997
Get-Service SplunkForwarder
```

## Automatic indexer bootstrap with environment variables

The extension can create the `wineventlog` and `web` indexes and enable the cooked TCP receiver automatically through the Splunk management REST API. Copy the supplied example without committing the resulting secret file:

```bash
cp extensions/splunk_uf/.env.example extensions/splunk_uf/.env
# Edit GOAD_SPLUNK_INDEXER_PASSWORD in .env.
set -a
source extensions/splunk_uf/.env
set +a
./goad.sh -t install -l GOAD -p libvirt -i <instance-id>
```

`GOAD_SPLUNK_INDEXER_PASSWORD` enables bootstrap automatically. The most useful variables are:

```text
GOAD_SPLUNK_INDEXER_HOST         management API and default receiver host
GOAD_SPLUNK_INDEXER_API_PORT     management API port (default 8089)
GOAD_SPLUNK_INDEXER_USER         indexer administrator (default admin)
GOAD_SPLUNK_INDEXER_PASSWORD     indexer administrator password
GOAD_SPLUNK_UF_PASSWORD          required UF-local administrator password
GOAD_SPLUNK_RECEIVER_HOST        optional data receiver host override
GOAD_SPLUNK_RECEIVER_PORT        cooked TCP port (default 9997)
GOAD_SPLUNK_WINDOWS_INDEX        default wineventlog
GOAD_SPLUNK_WEB_INDEX            default web
GOAD_SPLUNK_INDEXER_REQUIRED     fail installation if bootstrap fails
```

The password is read only from the controller process environment and all API tasks use Ansible `no_log`; it is not written to the Windows hosts. With a jumpbox/runner provisioner, these variables must also be exported into that provisioning environment.

This is still direct forwarding: the API is used once for receiver/index bootstrap, and no Deployment Server or `deploymentclient.conf` is configured. If no indexer password is exported, bootstrap is skipped and UF installation continues with the configured receiver destination.

The controller must be able to reach management port 8089. If Splunk runs in Docker, publish 8089 or attach the controller to the same network. Port 9997 must be reachable from the Windows guests. The REST bootstrap targets a standalone Splunk Enterprise instance; indexer clusters should manage `indexes.conf` through the cluster manager instead.

## Receiver requirements

Without automatic bootstrap, the Splunk receiving tier must already have TCP/9997 enabled and the indexes `wineventlog` and `web` must exist. TCP/8089 is needed only while automatic bootstrap is enabled.

## Remove

```text
uninstall_extension splunk_uf
```
