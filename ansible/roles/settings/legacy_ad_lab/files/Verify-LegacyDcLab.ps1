# Isolated lab prerequisite checks only; this script does not exploit a DC.
$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
$version = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$computer = Get-CimInstance Win32_ComputerSystem
if ($computer.DomainRole -lt 4 -or [int]$version.CurrentBuildNumber -ne 17763 -or
    $null -eq $version.UBR -or [int]$version.UBR -ge 1397) {
    throw 'Selected lab DC must be Windows Server 2019 build 17763 before the August 2020 Netlogon fix; use the selected historical box. No update will be uninstalled.'
}
Import-Module ActiveDirectory -ErrorAction Stop
$domain = Get-ADDomain -Server $env:COMPUTERNAME
$object = Get-ADObject -Server $env:COMPUTERNAME -Identity $domain.DistinguishedName -Properties ms-DS-MachineAccountQuota
if ([int]$object.'ms-DS-MachineAccountQuota' -le 0) {
    # Preserve the default GOAD-style account-creation precondition for noPac.
    Set-ADObject -Server $env:COMPUTERNAME -Identity $domain.DistinguishedName -Replace @{'ms-DS-MachineAccountQuota'=[int]10}
    $Ansible.Changed = $true
}
$object = Get-ADObject -Server $env:COMPUTERNAME -Identity $domain.DistinguishedName -Properties ms-DS-MachineAccountQuota
if ([int]$object.'ms-DS-MachineAccountQuota' -le 0) { throw 'Lab machine-account quota readback failed' }
foreach ($name in @('Netlogon','Kdc')) {
    if ((Get-Service $name).Status -ne 'Running') { throw "Required DC service is not running: $name" }
}
$update = Get-CimInstance Win32_Service -Filter "Name='wuauserv'"
if ($update.StartMode -ne 'Disabled' -or $update.State -ne 'Stopped') {
    throw 'Windows Update is not disabled on the selected historical lab DC'
}
[pscustomobject]@{host=$env:COMPUTERNAME;build=([string]$version.CurrentBuildNumber+'.'+$version.UBR);
    role='isolated historical DC';machine_account_quota=[int]$object.'ms-DS-MachineAccountQuota';
    update_disabled=$true;legacy_build_preconditions=$true;exploit_tested=$false}
