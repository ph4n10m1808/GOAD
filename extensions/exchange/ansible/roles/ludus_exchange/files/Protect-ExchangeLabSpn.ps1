[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Prepare', 'Restore', 'Recover')][string]$Action,
    [Parameter(Mandatory)][string]$ChildDC,
    [Parameter(Mandatory)][string]$ChildDomain,
    [Parameter(Mandatory)][string]$RootDomain
)
$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
$directory = 'C:\ProgramData\GOAD-Exchange-SPN-State'
$journal = Join-Path $directory 'pending.json'
if ($Action -eq 'Prepare' -or (Test-Path -LiteralPath $journal)) {
    # An interrupted controller can leave native Setup running. Preserve its
    # suspended SPN/journal until it exits, including during recovery/always.
    if (@(Get-Process Setup,ExSetup -ErrorAction SilentlyContinue).Count -gt 0) {
        throw 'Exchange Setup is still running; preserve pending SPN recovery state'
    }
}
# The first invocation precedes Exchange prerequisites, including RSAT.
if ($Action -ne 'Prepare' -and -not (Test-Path -LiteralPath $journal)) {
    [pscustomobject]@{action=$Action;pending=$false}
    return
}
Import-Module ActiveDirectory -ErrorAction Stop
if ($ChildDomain -notmatch '^[a-z0-9.-]+$' -or $RootDomain -notmatch '^[a-z0-9.-]+$' -or
    $ChildDC -notmatch '^[a-z0-9.-]+$' -or -not $ChildDomain.EndsWith('.' + $RootDomain)) {
    throw 'Unexpected GOAD-Light domain identity'
}
$domain = Get-ADDomain -Server $ChildDC
if ($domain.DNSRoot -ne $ChildDomain -or $domain.ParentDomain -ne $RootDomain) {
    throw 'Child controller/domain differs from selected lab inventory'
}
$computer = Get-ADComputer -Identity $ChildDC.Split('.')[0] -Server $ChildDC -Properties dNSHostName
if ($computer.DNSHostName -ne $ChildDC) { throw 'Child controller computer identity differs' }
$user = Get-ADUser -Identity 'jon.snow' -Server $ChildDC
$spn = 'CIFS/' + $ChildDC
$sid = $user.SID.Value
$allowed = @('S-1-5-18', 'S-1-5-32-544')

function Assert-PrivatePath([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'SPN journal path is a reparse point' }
    $acl = Get-Acl -LiteralPath $Path
    foreach ($ace in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($ace.AccessControlType -ne 'Allow' -or $ace.IdentityReference.Value -notin $allowed) {
            throw 'SPN journal permissions are not administrator/SYSTEM-only'
        }
    }
    if ($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]).Count -lt 2) {
        throw 'SPN journal permissions lack required administrators/SYSTEM access'
    }
}
function Get-SpnOwner {
    # Exact, bounded forest lookup; this child GC owns its writable domain copy.
    @(Get-ADObject -LDAPFilter "(servicePrincipalName=$spn)" -SearchBase '' -Server ($ChildDC + ':3268') -Properties objectSid -ResultSetSize 2)
}
function Restore-Pending {
    if (-not (Test-Path -LiteralPath $journal)) { return }
    Assert-PrivatePath $directory
    Assert-PrivatePath $journal
    $record = Get-Content -LiteralPath $journal -Raw | ConvertFrom-Json
    if ($record.version -ne 1 -or $record.account_sid -ne $sid -or $record.spn -ne $spn -or
        $record.child_dc -ne $ChildDC -or $record.computer -ne $env:COMPUTERNAME) {
        throw 'SPN recovery journal identity differs; preserve it for review'
    }
    $owners = @(Get-SpnOwner)
    if ($owners.Count -gt 1 -or ($owners.Count -eq 1 -and $owners[0].objectSid.Value -ne $sid)) {
        throw 'SPN now belongs to another account; preserve recovery journal'
    }
    if ($owners.Count -eq 0) {
        Set-ADUser -Identity $user.DistinguishedName -Server $ChildDC -ServicePrincipalNames @{Add=$spn}
        $Ansible.Changed = $true
    }
    $owners = @(Get-SpnOwner)
    if ($owners.Count -ne 1 -or $owners[0].objectSid.Value -ne $sid) {
        throw 'Restored lab SPN ownership verification failed; preserve journal'
    }
    Remove-Item -LiteralPath $journal -ErrorAction Stop
    $Ansible.Changed = $true
}

if (Test-Path -LiteralPath $directory) { Assert-PrivatePath $directory }
Restore-Pending
if ($Action -eq 'Prepare') {
    $owners = @(Get-SpnOwner)
    if ($owners.Count -eq 0) {
        # A lab that has not enabled this scenario needs no compatibility change.
        [pscustomobject]@{action=$Action;spn=$spn;pending=$false}
        return
    }
    if ($owners.Count -ne 1 -or $owners[0].objectSid.Value -ne $sid) {
        throw 'Expected exact constrained-delegation SPN on jon.snow; refusing suspension'
    }
    if (-not (Test-Path -LiteralPath $directory)) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($identity in $allowed) {
            $principal = New-Object Security.Principal.SecurityIdentifier($identity)
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($principal, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
            $acl.AddAccessRule($rule)
        }
        [IO.Directory]::CreateDirectory($directory, $acl) | Out-Null
    }
    Assert-PrivatePath $directory
    $record = @{version=1;account_sid=$sid;spn=$spn;child_dc=$ChildDC;computer=$env:COMPUTERNAME}
    $bytes = [Text.Encoding]::UTF8.GetBytes(($record | ConvertTo-Json -Compress))
    $file = [IO.File]::Open($journal, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $file.Write($bytes, 0, $bytes.Length); $file.Flush($true) } finally { $file.Dispose() }
    Assert-PrivatePath $journal
    Set-ADUser -Identity $user.DistinguishedName -Server $ChildDC -ServicePrincipalNames @{Remove=$spn}
    $Ansible.Changed = $true
    if (@(Get-SpnOwner).Count -ne 0) { throw 'SPN suspension verification failed; preserve journal' }
}
[pscustomobject]@{action=$Action;spn=$spn;account_sid=$sid;pending=(Test-Path -LiteralPath $journal)}
