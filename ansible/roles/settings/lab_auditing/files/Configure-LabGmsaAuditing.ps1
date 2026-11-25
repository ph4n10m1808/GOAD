param(
    [Parameter(Mandatory=$true)][string]$AccountName,
    [int]$MaxAttempts = 12,
    [int]$RetryDelaySeconds = 5
)
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop
$Ansible.Changed = $false
$account = $null
for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    try {
        $account = Get-ADServiceAccount -Server $env:COMPUTERNAME -Identity $AccountName -ErrorAction Stop
        break
    } catch {
        if ($attempt -ge $MaxAttempts) { throw }
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}
$schema = (Get-ADRootDSE -Server $env:COMPUTERNAME).schemaNamingContext
$attribute = @(Get-ADObject -Server $env:COMPUTERNAME -SearchBase $schema -LDAPFilter '(lDAPDisplayName=msDS-ManagedPassword)' -Properties schemaIDGUID)
if ($attribute.Count -ne 1) { throw 'Cannot identify the managed-password schema attribute' }
$passwordGuid = [Guid]::new([byte[]]$attribute[0].schemaIDGUID)
$everyone = [System.Security.Principal.SecurityIdentifier]'S-1-1-0'
$flags = [System.Security.AccessControl.AuditFlags]'Success,Failure'
$path = 'AD:\' + $account.DistinguishedName
$acl = Get-Acl -Path $path -Audit
$dacl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)
$checks = @(
    @{rights=[System.DirectoryServices.ActiveDirectoryRights]'ReadProperty,ExtendedRight'; object=$passwordGuid},
    @{rights=[System.DirectoryServices.ActiveDirectoryRights]'WriteProperty,WriteDacl,WriteOwner,Delete'; object=[Guid]::Empty}
)
foreach ($check in $checks) {
    $present = @($acl.GetAuditRules($true,$false,[System.Security.Principal.SecurityIdentifier]) | Where-Object {
        $_.IdentityReference -eq $everyone -and $_.ObjectType -eq $check.object -and
        ($_.ActiveDirectoryRights -band $check.rights) -eq $check.rights -and
        ($_.AuditFlags -band $flags) -eq $flags -and
        $_.InheritanceType -eq [System.DirectoryServices.ActiveDirectorySecurityInheritance]::None
    })
    if ($present.Count -eq 0) {
        $rule = New-Object System.DirectoryServices.ActiveDirectoryAuditRule($everyone,$check.rights,$flags,$check.object)
        $acl.AddAuditRule($rule)
        $Ansible.Changed = $true
    }
}
if ($Ansible.Changed) {
    # Audit only; do not widen password retrieval or any other access permission.
    $entry = New-Object System.DirectoryServices.DirectoryEntry('LDAP://' + $account.DistinguishedName)
    try {
        # Bypass the PowerShell ADSI property adapter (Options can resolve as LDAP attributes).
        $entry.psbase.Options.SecurityMasks = [System.DirectoryServices.SecurityMasks]::Sacl
        $entry.psbase.ObjectSecurity = $acl
        $entry.psbase.CommitChanges()
    } finally { $entry.psbase.Dispose() }
}
$readback = Get-Acl -Path $path -Audit
if ($readback.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access) -ne $dacl) {
    throw 'gMSA auditing changed access permissions'
}
foreach ($check in $checks) {
    $verified = @($readback.GetAuditRules($true,$false,[System.Security.Principal.SecurityIdentifier]) | Where-Object {
        $_.IdentityReference -eq $everyone -and $_.ObjectType -eq $check.object -and
        ($_.ActiveDirectoryRights -band $check.rights) -eq $check.rights -and
        ($_.AuditFlags -band $flags) -eq $flags -and
        $_.InheritanceType -eq [System.DirectoryServices.ActiveDirectorySecurityInheritance]::None
    })
    if ($verified.Count -eq 0) { throw 'Selected gMSA audit rule readback failed' }
}
[pscustomobject]@{account=$AccountName;password_attribute_guid=$passwordGuid.ToString();audit_rules_verified=$true;dacl_preserved=$true}
