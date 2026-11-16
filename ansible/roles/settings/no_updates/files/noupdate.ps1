$ErrorActionPreference = 'Stop'

# set the Windows Update service to "disabled"
sc.exe config wuauserv start=disabled
if ($LASTEXITCODE -ne 0) {
    throw "Cannot disable Windows Update: sc.exe returned $LASTEXITCODE"
}

# stop the service, in case it is running
#sc.exe stop wuauserv

$ServiceName = 'wuauserv'
$arrService = Get-Service -Name $ServiceName
if ($arrService.Status -ne 'Stopped')
{
	Stop-Service $ServiceName
}

# Disable autoupdate
#$AUSettings = (New-Object -com "Microsoft.Update.AutoUpdate").Settings
#$AUSettings.NotificationLevel = 1
#$AUSettings.Save

if (-not (Test-Path 'HKLM:\Software\Policies\Microsoft\Windows\WindowsUpdate')) {
    New-Item -Path 'HKLM:\Software\Policies\Microsoft\Windows' -Name 'WindowsUpdate' | Out-Null
}
if (-not (Test-Path 'HKLM:\Software\Policies\Microsoft\Windows\WindowsUpdate\AU')) {
    New-Item -Path 'HKLM:\Software\Policies\Microsoft\Windows\WindowsUpdate' -Name 'AU' | Out-Null
}
New-ItemProperty 'HKLM:\Software\Policies\Microsoft\Windows\WindowsUpdate\AU' -name 'NoAutoUpdate' -value '1' -propertyType "DWord" -force

$arrService.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
$serviceState = Get-CimInstance Win32_Service -Filter "Name='wuauserv'"
$policy = Get-ItemProperty 'HKLM:\Software\Policies\Microsoft\Windows\WindowsUpdate\AU'
if ($serviceState.StartMode -ne 'Disabled' -or $serviceState.State -ne 'Stopped' -or $policy.NoAutoUpdate -ne 1) {
    throw 'Windows Update disable readback failed'
}
[pscustomobject]@{Service='wuauserv'; StartMode=$serviceState.StartMode; State=$serviceState.State; NoAutoUpdate=$policy.NoAutoUpdate}

