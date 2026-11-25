[CmdletBinding()]
param(
    [string[]]$TemplateNames = @('ESC1','ESC2','ESC3-CRA','ESC3','ESC4','ESC9','ESC13','WebServer'),
    [bool]$AuditEsc13Policy = $true,
    [bool]$RequireAllTemplates = $false
)

$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop
$Ansible.Changed = $false
$config = (Get-ADRootDSE).configurationNamingContext
$pki = "CN=Public Key Services,CN=Services,$config"
$targets = @()
$missingTemplates = @()
foreach ($templateName in $TemplateNames) {
    $dn = "CN=$templateName,CN=Certificate Templates,$pki"
    $found = @(Get-ADObject -SearchBase "CN=Certificate Templates,$pki" -SearchScope OneLevel -LDAPFilter "(cn=$templateName)" -ErrorAction Stop)
    if ($found.Count -eq 1) {
        $targets += $found[0].DistinguishedName
    } elseif ($found.Count -gt 1) {
        throw "Ambiguous certificate template object for auditing: $templateName"
    } elseif ($RequireAllTemplates) {
        throw "Required certificate template object is missing: $dn"
    } else {
        $missingTemplates += $templateName
    }
}
$esc13PolicyAudited = $false
if ($AuditEsc13Policy) {
    $policies = @(Get-ADObject -SearchBase "CN=OID,$pki" -SearchScope OneLevel -Filter * -Properties displayName |
        Where-Object { $_.displayName -eq 'IssuancePolicyESC13' })
    if ($policies.Count -gt 1) { throw 'Cannot identify exactly one ESC13 issuance policy to audit' }
    if ($policies.Count -eq 1) {
        $targets += $policies[0].DistinguishedName
        $esc13PolicyAudited = $true
    } elseif ($RequireAllTemplates) {
        throw 'Required ESC13 issuance policy is missing'
    }
}
$everyone = [System.Security.Principal.SecurityIdentifier]'S-1-1-0'
$rights = [System.DirectoryServices.ActiveDirectoryRights]'WriteProperty,WriteDacl,WriteOwner,Delete'
$auditFlags = [System.Security.AccessControl.AuditFlags]'Success,Failure'
foreach ($dn in $targets) {
    $path = 'AD:\' + $dn
    $acl = Get-Acl -Path $path -Audit
    $dacl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)
    $present = @($acl.GetAuditRules($true,$false,[System.Security.Principal.SecurityIdentifier]) | Where-Object {
        $_.IdentityReference -eq $everyone -and
        ($_.ActiveDirectoryRights -band $rights) -eq $rights -and
        ($_.AuditFlags -band $auditFlags) -eq $auditFlags -and
        $_.ObjectType -eq [Guid]::Empty -and
        $_.InheritanceType -eq [System.DirectoryServices.ActiveDirectorySecurityInheritance]::None
    })
    if ($present.Count -eq 0) {
        $rule = New-Object System.DirectoryServices.ActiveDirectoryAuditRule($everyone,$rights,$auditFlags)
        $acl.AddAuditRule($rule)
        # Write only the audit section; preserve the intentionally weak DACL.
        $entry = New-Object System.DirectoryServices.DirectoryEntry('LDAP://' + $dn)
        try {
            # Use the CLR member, not the PowerShell LDAP-property adapter.
            $entry.psbase.Options.SecurityMasks = [System.DirectoryServices.SecurityMasks]::Sacl
            $entry.psbase.ObjectSecurity = $acl
            $entry.psbase.CommitChanges()
        } finally { $entry.psbase.Dispose() }
        $Ansible.Changed = $true
    }
    $readback = Get-Acl -Path $path -Audit
    if ($readback.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access) -ne $dacl) {
        throw "Certificate object auditing changed its lab access permissions: $dn"
    }
    $verified = $readback.GetAuditRules($true,$false,[System.Security.Principal.SecurityIdentifier]) |
        Where-Object { $_.IdentityReference -eq $everyone -and
            ($_.ActiveDirectoryRights -band $rights) -eq $rights -and
            ($_.AuditFlags -band $auditFlags) -eq $auditFlags -and $_.ObjectType -eq [Guid]::Empty -and
            $_.InheritanceType -eq [System.DirectoryServices.ActiveDirectorySecurityInheritance]::None }
    if (-not $verified) { throw "Missing selected object audit rule: $dn" }
}
[pscustomobject]@{audited_objects=$targets.Count;missing_templates=$missingTemplates;esc13_policy_audited=$esc13PolicyAudited;scope='Selected ESC templates and optional ESC13 issuance policy';dacl_unchanged=$true}
