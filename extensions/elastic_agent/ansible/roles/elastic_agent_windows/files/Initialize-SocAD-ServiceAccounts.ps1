param(
    [Parameter(Mandatory=$true)][Security.SecureString]$SiemPassword,
    [Parameter(Mandatory=$true)][Security.SecureString]$SoarPassword,
    [string]$RecoverCreatedAccountGuid
)
$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
Import-Module ActiveDirectory
$domain = Get-ADDomain
if ($domain.DNSRoot -ne 'sevenkingdoms.local') { throw 'Expected the GOAD root domain; refusing another directory' }
$root = 'C:\ProgramData\Elastic\soc-service-accounts'
if (-not (Test-Path -LiteralPath $root)) { [IO.Directory]::CreateDirectory($root) | Out-Null }
$acl = Get-Acl -LiteralPath $root
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        (New-Object Security.Principal.SecurityIdentifier($sid)),
        'FullControl','ContainerInherit, ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $root -AclObject $acl
function Write-SocAccountReceipt {
    param([string]$Path, [object]$Record)
    $temp = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($Record | ConvertTo-Json -Compress))
        $stream = [IO.File]::Open($temp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        if (Test-Path -LiteralPath $Path) {
            $previous = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.previous'
            [IO.File]::Replace($temp,$Path,$previous)
            if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous }
        }
        else { [IO.File]::Move($temp,$Path) }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
    }
}
$lock = [IO.File]::Open((Join-Path $root 'accounts.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try {
$rows = @()
foreach ($legacy in @('soc-siem','soc-soar')) {
    if (Get-ADUser -Filter {SamAccountName -eq $legacy}) {
        throw "Legacy account exists; review migration before creating underscore identities: $legacy"
    }
}
foreach ($name in @('soc_siem','soc_soar')) {
    $description = if ($name -eq 'soc_siem') {'SOC SIEM read-only directory identity'} else {'SOC SOAR standby identity; response delegation not granted'}
    $secure = if ($name -eq 'soc_siem') { $SiemPassword } else { $SoarPassword }
    $plain = (New-Object Management.Automation.PSCredential('unused',$secure)).GetNetworkCredential().Password
    $passwordBytes = [Text.Encoding]::UTF8.GetBytes($plain)
    $plain = $null
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $fingerprint = [Convert]::ToBase64String($sha.ComputeHash($passwordBytes)) }
    finally { [Array]::Clear($passwordBytes,0,$passwordBytes.Length); $sha.Dispose() }
    $recordPath = Join-Path $root ($name + '.json')
    $account = Get-ADUser -Filter {SamAccountName -eq $name} -Properties Description,MemberOf,PrimaryGroupID,Enabled,ObjectGUID,UserPrincipalName
    $record = $null
    if (Test-Path -LiteralPath $recordPath) { $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json }
    if ($record -and ($record.domain_sid -ne $domain.DomainSID.Value -or $record.password_fingerprint -ne $fingerprint)) {
        throw "Controller credentials or domain do not match the managed receipt; refusing rotation/adoption: $name"
    }
    if ($account) {
        # An interrupted creation may leave the original private intent without a GUID.
        # Recover only a caller-reviewed exact GUID in this same domain, never by name alone.
        if ($record -and -not $record.object_guid -and $RecoverCreatedAccountGuid -and
            $account.ObjectGUID.ToString() -eq $RecoverCreatedAccountGuid -and
            $record.domain_sid -eq $domain.DomainSID.Value -and $account.Description -eq $description -and
            $account.PrimaryGroupID -eq 513 -and @($account.MemberOf).Count -eq 0) {
            $record.object_guid = $account.ObjectGUID.ToString()
            Write-SocAccountReceipt -Path $recordPath -Record $record
        }
        if (-not $record -or $account.Description -ne $description -or $record.object_guid -ne $account.ObjectGUID.ToString()) {
            throw "Existing account has no matching managed receipt; do not rotate or adopt: $name"
        }
    } else {
        # Controller stores generated passwords privately before this call.
        # The guest receipt proves ownership; it never contains passwords.
        if (-not $record) {
            $record = [pscustomobject]@{sam_account_name=$name; domain_sid=$domain.DomainSID.Value; object_guid=$null; password_fingerprint=$fingerprint}
            Write-SocAccountReceipt -Path $recordPath -Record $record
        } else {
            throw "Account absent but creation intent exists; recover deliberately rather than rotate: $name"
        }
        New-ADUser -Name $name -SamAccountName $name -UserPrincipalName "$name@$($domain.DNSRoot)" `
            -Path "CN=Users,$($domain.DistinguishedName)" -Description $description `
            -AccountPassword $secure -Enabled ($name -eq 'soc_siem') -ChangePasswordAtLogon $false -PasswordNeverExpires $false
        $Ansible.Changed = $true
        $account = Get-ADUser $name -Properties Description,MemberOf,PrimaryGroupID,Enabled,ObjectGUID,UserPrincipalName
        $record.object_guid = $account.ObjectGUID.ToString()
        Write-SocAccountReceipt -Path $recordPath -Record $record
    }
    if ($record.domain_sid -ne $domain.DomainSID.Value -or $account.PrimaryGroupID -ne 513 -or @($account.MemberOf).Count -ne 0) {
        throw "Unexpected domain or additional membership: $name"
    }
    if ($account.Enabled -ne ($name -eq 'soc_siem')) { throw "Unexpected enabled state; no implicit change: $name" }
    $rows += [pscustomobject]@{name=$name; upn=$account.UserPrincipalName; dn=$account.DistinguishedName;
        object_guid=$account.ObjectGUID.ToString(); enabled=$account.Enabled; additional_groups=@($account.MemberOf);
        domain_sid=$domain.DomainSID.Value}
}
[pscustomobject]@{domain=$domain.DNSRoot; accounts=$rows; response_delegation=$false} | ConvertTo-Json -Depth 5 -Compress
} finally { $lock.Dispose() }
