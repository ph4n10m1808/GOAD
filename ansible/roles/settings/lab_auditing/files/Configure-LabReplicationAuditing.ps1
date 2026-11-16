$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
Import-Module ActiveDirectory -ErrorAction Stop
$domainDN = (Get-ADRootDSE -Server $env:COMPUTERNAME).defaultNamingContext
if ([string]::IsNullOrWhiteSpace($domainDN)) { throw 'Local domain naming context is missing' }
$path = 'LDAP://' + $env:COMPUTERNAME + '/' + $domainDN
$everyone = [System.Security.Principal.SecurityIdentifier]'S-1-1-0'
$flags = [System.Security.AccessControl.AuditFlags]'Success,Failure'
$checks = @(
    # Replication access is checked on the domain root, not descendant objects.
    @{rights=[System.DirectoryServices.ActiveDirectoryRights]::ExtendedRight; object=[Guid]'1131f6aa-9c07-11d1-f79f-00c04fc2dcd2'},
    @{rights=[System.DirectoryServices.ActiveDirectoryRights]::ExtendedRight; object=[Guid]'1131f6ad-9c07-11d1-f79f-00c04fc2dcd2'},
    @{rights=[System.DirectoryServices.ActiveDirectoryRights]::ExtendedRight; object=[Guid]'89e95b76-444d-4c62-991a-0facbeda640c'},
    @{rights=[System.DirectoryServices.ActiveDirectoryRights]'WriteDacl,WriteOwner'; object=[Guid]::Empty}
)
$entry = New-Object System.DirectoryServices.DirectoryEntry($path)
try {
    # Use CLR members explicitly; the ADSI adapter can treat Options as an LDAP attribute.
    $entry.psbase.Options.SecurityMasks = [System.DirectoryServices.SecurityMasks]'Dacl,Sacl'
    $acl = $entry.psbase.ObjectSecurity
    $dacl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)
    foreach ($check in $checks) {
        $present = @($acl.GetAuditRules($true,$false,[System.Security.Principal.SecurityIdentifier]) | Where-Object {
            $_.IdentityReference -eq $everyone -and $_.ObjectType -eq $check.object -and
            ($_.ActiveDirectoryRights -band $check.rights) -eq $check.rights -and
            ($_.AuditFlags -band $flags) -eq $flags -and
            $_.InheritanceType -eq [System.DirectoryServices.ActiveDirectorySecurityInheritance]::None
        })
        if ($present.Count -eq 0) {
            $acl.AddAuditRule((New-Object System.DirectoryServices.ActiveDirectoryAuditRule($everyone,$check.rights,$flags,$check.object)))
            $Ansible.Changed = $true
        }
    }
    if ($Ansible.Changed) {
        # Write only SACL auditing. No access grants, inheritance or vulnerable lab rights change.
        $entry.psbase.Options.SecurityMasks = [System.DirectoryServices.SecurityMasks]::Sacl
        $entry.psbase.ObjectSecurity = $acl
        $entry.psbase.CommitChanges()
    }
} finally { $entry.psbase.Dispose() }
$readback = New-Object System.DirectoryServices.DirectoryEntry($path)
try {
    $readback.psbase.Options.SecurityMasks = [System.DirectoryServices.SecurityMasks]'Dacl,Sacl'
    $verifiedAcl = $readback.psbase.ObjectSecurity
    if ($verifiedAcl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access) -ne $dacl) {
        throw 'Domain replication auditing changed access permissions'
    }
    foreach ($check in $checks) {
        $verified = @($verifiedAcl.GetAuditRules($true,$false,[System.Security.Principal.SecurityIdentifier]) | Where-Object {
            $_.IdentityReference -eq $everyone -and $_.ObjectType -eq $check.object -and
            ($_.ActiveDirectoryRights -band $check.rights) -eq $check.rights -and
            ($_.AuditFlags -band $flags) -eq $flags -and
            $_.InheritanceType -eq [System.DirectoryServices.ActiveDirectorySecurityInheritance]::None
        })
        if ($verified.Count -eq 0) { throw 'Domain replication audit rule readback differs' }
    }
} finally { $readback.psbase.Dispose() }
$Ansible.Result = @{domain=$domainDN;audit_rules_verified=$checks.Count;dacl_preserved=$true;inheritance='None'}
