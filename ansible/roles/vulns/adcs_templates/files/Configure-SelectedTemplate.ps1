# Native GOAD template import with explicit DC selection and lab ACL readback.
param(
    [Parameter(Mandatory=$true)][string]$TemplateName,
    [Parameter(Mandatory=$true)][string]$TemplateFile,
    [Parameter(Mandatory=$true)][string]$EnrollPrincipal,
    [string]$ControlPrincipal = ''
)
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop
Import-Module ADCSTemplate -ErrorAction Stop
$Ansible.Changed = $false
$server = $env:COMPUTERNAME
$templates = @(Get-ADCSTemplate -DisplayName $TemplateName -Server $server)
if ($templates.Count -gt 1) { throw 'Selected template display name is ambiguous' }
if ($templates.Count -eq 0) {
    New-ADCSTemplate -DisplayName $TemplateName -Server $server -JSON (Get-Content -LiteralPath $TemplateFile -Raw) -Identity $EnrollPrincipal -Publish | Out-Null
    $Ansible.Changed = $true
    $templates = @(Get-ADCSTemplate -DisplayName $TemplateName -Server $server)
}
if ($templates.Count -ne 1) { throw 'Selected template was not created' }
$template = $templates[0]
$path = 'AD:\' + $template.DistinguishedName
$enrollSid = ([System.Security.Principal.NTAccount]$EnrollPrincipal).Translate([System.Security.Principal.SecurityIdentifier])
$enrollGuid = [Guid]'0e10c968-78fb-11d2-90d4-00c04f79dc55'
$enrollRules = @((Get-Acl $path).Access | Where-Object {
    $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $enrollSid.Value -and
    [string]$_.AccessControlType -eq 'Allow' -and
    ($_.ActiveDirectoryRights -band [System.DirectoryServices.ActiveDirectoryRights]::ExtendedRight) -and
    $_.ObjectType -in @($enrollGuid,[Guid]::Empty)
})
if ($enrollRules.Count -eq 0) {
    Set-ADCSTemplateACL -DisplayName $TemplateName -Server $server -Type Allow -Identity $EnrollPrincipal -Enroll
    $Ansible.Changed = $true
}
if (-not [string]::IsNullOrWhiteSpace($ControlPrincipal)) {
    # ESC4 deliberately grants control of this one template; preserve other ACEs.
    $sid = ([System.Security.Principal.NTAccount]$ControlPrincipal).Translate([System.Security.Principal.SecurityIdentifier])
    $acl = Get-Acl $path
    $genericAll = [System.DirectoryServices.ActiveDirectoryRights]::GenericAll
    $rules = @($acl.Access | Where-Object {
        $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $sid.Value -and
        [string]$_.AccessControlType -eq 'Allow' -and
        ($_.ActiveDirectoryRights -band $genericAll) -eq $genericAll -and $_.ObjectType -eq [Guid]::Empty
    })
    if ($rules.Count -eq 0) {
        $rule = New-Object System.DirectoryServices.ActiveDirectoryAccessRule($sid,$genericAll,[System.Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
        Set-Acl -Path $path -AclObject $acl
        $Ansible.Changed = $true
    }
    $verified = @((Get-Acl $path).Access | Where-Object {
        $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $sid.Value -and
        [string]$_.AccessControlType -eq 'Allow' -and
        ($_.ActiveDirectoryRights -band $genericAll) -eq $genericAll -and $_.ObjectType -eq [Guid]::Empty
    })
    if ($verified.Count -eq 0) { throw 'Selected template control permission readback failed' }
}
$verifiedEnroll = @((Get-Acl $path).Access | Where-Object {
    $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $enrollSid.Value -and
    [string]$_.AccessControlType -eq 'Allow' -and
    ($_.ActiveDirectoryRights -band [System.DirectoryServices.ActiveDirectoryRights]::ExtendedRight) -and
    $_.ObjectType -in @($enrollGuid,[Guid]::Empty)
})
if ($verifiedEnroll.Count -eq 0) { throw 'Selected template enrollment permission readback failed' }
$config = (Get-ADRootDSE -Server $server).configurationNamingContext
$authorities = @(Get-ADObject -Server $server -SearchBase "CN=Enrollment Services,CN=Public Key Services,CN=Services,$config" -LDAPFilter '(objectClass=pKIEnrollmentService)' -Properties certificateTemplates)
if ($authorities.Count -eq 0) { throw 'No CA is available for selected template publication' }
# Preserve this native caller's publish-to-all-lab-CAs contract on rerun too.
foreach ($ca in $authorities) {
    if ($template.Name -notin @($ca.certificateTemplates)) {
        Set-ADObject -Server $server -Identity $ca.DistinguishedName -Add @{certificateTemplates=$template.Name}
        $Ansible.Changed = $true
    }
    $readback = Get-ADObject -Server $server -Identity $ca.DistinguishedName -Properties certificateTemplates
    if ($template.Name -notin @($readback.certificateTemplates)) { throw 'Selected template publication readback failed' }
}
[pscustomobject]@{template=$template.Name;enrollment_verified=$true;control_principal=$ControlPrincipal;published_ca_count=$authorities.Count}
