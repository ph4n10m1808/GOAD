$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\Elastic'
New-Item -Path $root -ItemType Directory -Force | Out-Null
$backup = Join-Path $root 'audit-policy-before-soc.csv'
if (-not (Test-Path $backup)) {
    & auditpol.exe /backup "/file:$backup" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot preserve prior Windows audit policy' }
}
$script:RegistryBackupDirectory = $null
$script:RegistryBackups = @{}
function Backup-SocRegistryKey {
    param([Parameter(Mandatory=$true)][string]$Path)
    # For a missing policy key, preserve its closest existing parent before creation.
    $source = $Path
    while (-not (Test-Path -LiteralPath $source)) {
        $source = Split-Path -Path $source -Parent
        if (-not $source) { throw 'Cannot identify an existing registry backup source' }
    }
    if ($script:RegistryBackups.ContainsKey($source)) { return }
    if (-not $script:RegistryBackupDirectory) {
        $name = 'registry-before-soc-{0}-{1}-{2}' -f $env:COMPUTERNAME,
            [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'), [Guid]::NewGuid().ToString('N')
        $script:RegistryBackupDirectory = Join-Path $root $name
        New-Item -ItemType Directory -Path $script:RegistryBackupDirectory | Out-Null
        $acl = Get-Acl -LiteralPath $script:RegistryBackupDirectory
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                (New-Object System.Security.Principal.SecurityIdentifier($sid)),
                'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $script:RegistryBackupDirectory -AclObject $acl
    }
    $nativePath = $source.Replace('HKLM:\', 'HKLM\')
    $file = Join-Path $script:RegistryBackupDirectory ('key-{0:D2}.reg' -f $script:RegistryBackups.Count)
    & reg.exe export $nativePath $file | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $file) -or
        (Get-Item -LiteralPath $file).Length -eq 0) {
        throw "Cannot preserve native registry configuration: $source"
    }
    $script:RegistryBackups[$source] = $file
}
$names = @('Logon', 'Logoff', 'Special Logon', 'Account Lockout',
    'Credential Validation', 'Process Creation', 'Security System Extension',
    'Other Object Access Events', 'Detailed File Share', 'Audit Policy Change',
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
    @('HKLM:\Software\Policies\Microsoft\Windows\PowerShell\ModuleLogging', 'EnableModuleLogging', 1)
)
foreach ($item in $settings) {
    if (-not (Test-Path $item[0])) {
        Backup-SocRegistryKey -Path $item[0]
        New-Item -Path $item[0] -Force | Out-Null
    }
    $value = (Get-ItemProperty -Path $item[0] -Name $item[1] -ErrorAction SilentlyContinue).($item[1])
    if ($value -ne $item[2]) {
        Backup-SocRegistryKey -Path $item[0]
        New-ItemProperty -Path $item[0] -Name $item[1] -Value $item[2] -PropertyType DWord -Force | Out-Null
        $changed += $item[1]
    }
    if ((Get-ItemProperty -Path $item[0] -Name $item[1]).($item[1]) -ne $item[2]) {
        throw "Registry audit setting readback differs: $($item[1])"
    }
}
$modules = 'HKLM:\Software\Policies\Microsoft\Windows\PowerShell\ModuleLogging\ModuleNames'
if (-not (Test-Path $modules)) {
    Backup-SocRegistryKey -Path $modules
    New-Item -Path $modules -Force | Out-Null
}
if ((Get-ItemProperty -Path $modules -Name '*' -ErrorAction SilentlyContinue).'*' -ne '*') {
    Backup-SocRegistryKey -Path $modules
    New-ItemProperty -Path $modules -Name '*' -Value '*' -PropertyType String -Force | Out-Null
    $changed += 'PowerShell module wildcard'
}
if ((Get-ItemProperty -Path $modules -Name '*').'*' -ne '*') {
    throw 'PowerShell module wildcard readback differs'
}
foreach ($channel in @('Microsoft-Windows-PowerShell/Operational', 'Microsoft-Windows-TaskScheduler/Operational')) {
    & wevtutil.exe sl $channel /e:true
    if ($LASTEXITCODE -ne 0) { throw "Cannot enable channel $channel" }
}
$after = @(& auditpol.exe /get /category:* /r | ConvertFrom-Csv)
foreach ($name in $names) {
    if (($after | Where-Object { $_.Subcategory -eq $name }).'Inclusion Setting' -ne 'Success and Failure') {
        throw "Audit readback differs: $name"
    }
}
[pscustomobject]@{host=$env:COMPUTERNAME; utc=(Get-Date).ToUniversalTime().ToString('o');
    subcategories=$names.Count; changed=$changed; command_line=$true; powershell_scriptblock=$true;
    powershell_module=$true; audit_backup=$backup;
    registry_backups=@($script:RegistryBackups.Values)} | ConvertTo-Json -Compress
