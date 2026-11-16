$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\Elastic'
New-Item -Path $root -ItemType Directory -Force | Out-Null
# Operator requested native audit settings without creating guest backups.
$names = @('Logon', 'Logoff', 'Special Logon', 'Account Lockout',
    'Credential Validation', 'Process Creation', 'Security System Extension',
    'Other Object Access Events', 'File Share', 'Detailed File Share', 'Audit Policy Change',
    'Authentication Policy Change', 'User Account Management',
    'Computer Account Management', 'Security Group Management')
$isDC = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ProductOptions').ProductType -eq 'LanmanNT'
if ($isDC) {
    $names += @('Directory Service Access', 'Directory Service Changes',
        'Kerberos Authentication Service', 'Kerberos Service Ticket Operations')
}
if (Get-Service CertSvc -ErrorAction SilentlyContinue) { $names += 'Certification Services' }
$before = @(& auditpol.exe /get /category:* /r | ConvertFrom-Csv)
$changed = @()
foreach ($name in $names) {
    $row = @($before | Where-Object { $_.Subcategory -eq $name })
    if ($row.Count -ne 1) { throw "Cannot identify English GOAD audit subcategory: $name" }
    if ($row[0].'Inclusion Setting' -ne 'Success and Failure') {
        & auditpol.exe /set "/subcategory:$name" /success:enable /failure:enable | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Audit policy update failed: $name" }
        $changed += $name
    }
}
$settings = @(
    @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System\Audit', 'ProcessCreationIncludeCmdLine_Enabled', 1),
    @('HKLM:\Software\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging', 'EnableScriptBlockLogging', 1),
    @('HKLM:\Software\Policies\Microsoft\Windows\PowerShell\ModuleLogging', 'EnableModuleLogging', 1),
    # Audit only; do not set NTLM deny/block policy or harden deliberate lab conditions.
    @('HKLM:\SYSTEM\CurrentControlSet\Control\Lsa\MSV1_0', 'AuditReceivingNTLMTraffic', 2)
)
if ($isDC) { $settings += ,@('HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters', 'AuditNTLMInDomain', 7) }
# Outgoing value1 means audit, not deny. Preserve an existing deny value2.
$ntlmPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa\MSV1_0'
$outgoingNtlm = (Get-ItemProperty -Path $ntlmPath -Name RestrictSendingNTLMTraffic -ErrorAction SilentlyContinue).RestrictSendingNTLMTraffic
if ($null -eq $outgoingNtlm -or $outgoingNtlm -eq 0) {
    $settings += ,@($ntlmPath, 'RestrictSendingNTLMTraffic', 1)
}
foreach ($item in $settings) {
    if (-not (Test-Path $item[0])) {
        New-Item -Path $item[0] -Force | Out-Null
    }
    $value = (Get-ItemProperty -Path $item[0] -Name $item[1] -ErrorAction SilentlyContinue).($item[1])
    if ($value -ne $item[2]) {
        New-ItemProperty -Path $item[0] -Name $item[1] -Value $item[2] -PropertyType DWord -Force | Out-Null
        $changed += $item[1]
    }
    if ((Get-ItemProperty -Path $item[0] -Name $item[1]).($item[1]) -ne $item[2]) {
        throw "Registry audit setting readback differs: $($item[1])"
    }
}
$modules = 'HKLM:\Software\Policies\Microsoft\Windows\PowerShell\ModuleLogging\ModuleNames'
if (-not (Test-Path $modules)) {
    New-Item -Path $modules -Force | Out-Null
}
if ((Get-ItemProperty -Path $modules -Name '*' -ErrorAction SilentlyContinue).'*' -ne '*') {
    New-ItemProperty -Path $modules -Name '*' -Value '*' -PropertyType String -Force | Out-Null
    $changed += 'PowerShell module wildcard'
}
if ((Get-ItemProperty -Path $modules -Name '*').'*' -ne '*') {
    throw 'PowerShell module wildcard readback differs'
}
$enabledChannels = @()
$missingChannels = @()
$channels = @(
    'Application', 'Security', 'System', 'Windows PowerShell',
    'Microsoft-Windows-PowerShell/Operational', 'Microsoft-Windows-TaskScheduler/Operational',
    'Microsoft-Windows-PrintService/Operational', 'Microsoft-Windows-Bits-Client/Operational',
    'Microsoft-Windows-CodeIntegrity/Operational', 'Microsoft-Windows-GroupPolicy/Operational',
    'Microsoft-Windows-NTLM/Operational', 'Microsoft-Windows-SmbClient/Security',
    'Microsoft-Windows-SMBClient/Connectivity',
    'Microsoft-Windows-SMBServer/Audit', 'Microsoft-Windows-SMBServer/Connectivity',
    'Microsoft-Windows-SMBServer/Operational', 'Microsoft-Windows-SMBServer/Security',
    'Microsoft-Windows-TerminalServices-LocalSessionManager/Operational',
    'Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational',
    'Microsoft-Windows-RemoteDesktopServices-RdpCoreTS/Operational',
    'Microsoft-Windows-Security-Netlogon/Operational',
    'Microsoft-Windows-PrintService/Admin',
    'Microsoft-Windows-Windows Firewall With Advanced Security/Firewall',
    'Microsoft-Windows-WinRM/Operational', 'Microsoft-Windows-WMI-Activity/Operational',
    'Microsoft-Windows-AppLocker/EXE and DLL', 'Microsoft-Windows-AppLocker/MSI and Script',
    'Microsoft-Windows-AppLocker/Packaged app-Deployment', 'Microsoft-Windows-AppLocker/Packaged app-Execution',
    'Microsoft-Windows-Windows Defender/Operational', 'Microsoft-Windows-WindowsUpdateClient/Operational',
    'Setup',
    'Microsoft-Windows-CertificateServices-Deployment/Operational',
    'Microsoft-Windows-CertificateServicesClient-Lifecycle-System/Operational',
    'Microsoft-Windows-CertificateServicesClient-Lifecycle-User/Operational'
)
if ($isDC) { $channels += @('Active Directory Web Services','DFS Replication','Directory Service','DNS Server','Microsoft-Windows-DNSServer/Audit') }
foreach ($channel in $channels) {
    $currentChannel = Get-WinEvent -ListLog $channel -ErrorAction SilentlyContinue
    if (-not $currentChannel) {
        if ($channel -in @('Application','Security','System','Windows PowerShell','Microsoft-Windows-PowerShell/Operational','Microsoft-Windows-TaskScheduler/Operational')) { throw "Required Windows event channel missing: $channel" }
        $missingChannels += $channel
        continue
    }
    if ([string]$currentChannel.LogType -in @('Analytical','Analytic','Debug')) { throw "Refusing noisy trace channel: $channel" }
    if ($currentChannel.IsEnabled) { continue }
    & wevtutil.exe sl $channel /e:true
    if ($LASTEXITCODE -ne 0) { throw "Cannot enable channel $channel" }
    if (-not (Get-WinEvent -ListLog $channel).IsEnabled) { throw "Channel readback differs: $channel" }
    $enabledChannels += $channel
    $changed += $channel
}
$after = @(& auditpol.exe /get /category:* /r | ConvertFrom-Csv)
foreach ($name in $names) {
    if (($after | Where-Object { $_.Subcategory -eq $name }).'Inclusion Setting' -ne 'Success and Failure') {
        throw "Audit readback differs: $name"
    }
}
[pscustomobject]@{host=$env:COMPUTERNAME; utc=(Get-Date).ToUniversalTime().ToString('o');
    subcategories=$names.Count; changed=$changed; command_line=$true; powershell_scriptblock=$true;
    powershell_module=$true;
    enabled_channels=$enabledChannels; missing_channels=$missingChannels;
    ntlm_incoming_audit=$true; ntlm_domain_audit=$isDC} | ConvertTo-Json -Compress
