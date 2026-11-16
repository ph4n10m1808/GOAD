# Code from LUDUS ESC13 role
# Licence GPL-3.0
# https://github.com/badsectorlabs/ludus_adcs/blob/main/files/esc13.ps1

param(
    [Parameter(Mandatory=$true)]
    [string]$esc13group,
    
    [Parameter(Mandatory=$true)]
    [string]$esc13templateName
)


$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop

 # Function to generate a random hexadecimal string of a given length
 Function Get-RandomHex {
    param ([int]$Length)
    $Hex = '0123456789ABCDEF'
    $Return = ''
    1..$Length | ForEach-Object {
        $Return += $Hex.Substring((Get-Random -Minimum 0 -Maximum 16),1)
    }
    Return $Return
}

# Function to check if a given OID is unique
Function IsUniqueOID {
    param ($cn, $TemplateOID, $ConfigNC)
    $Search = Get-ADObject -Filter {cn -eq $cn -or msPKI-Cert-Template-OID -eq $TemplateOID} -SearchBase "CN=OID,CN=Public Key Services,CN=Services,$ConfigNC"
    If ($Search) {$False} Else {$True}
}

# Function to generate a new unique OID
Function New-TemplateOID {
    Param($ConfigNC)
    do {
        $OID_Part_1 = Get-Random -Minimum 10000000 -Maximum 99999999
        $OID_Part_2 = Get-Random -Minimum 10000000 -Maximum 99999999
        $OID_Part_3 = Get-RandomHex -Length 32
        $OID_Forest = Get-ADObject -Identity "CN=OID,CN=Public Key Services,CN=Services,$ConfigNC" -Properties msPKI-Cert-Template-OID |
            Select-Object -ExpandProperty msPKI-Cert-Template-OID
        $msPKICertTemplateOID = "$OID_Forest.$OID_Part_1.$OID_Part_2"
        $Name = "$OID_Part_2.$OID_Part_3"
    } until (IsUniqueOID -cn $Name -TemplateOID $msPKICertTemplateOID -ConfigNC $ConfigNC)
    Return @{
        TemplateOID  = $msPKICertTemplateOID
        TemplateName = $Name
    }
}

# Intentionally configure an ESC13 lab issuance-policy/group link.
$changed = $false
$ConfigNC = (Get-ADRootDSE).configurationNamingContext
$IssuanceName = 'IssuancePolicyESC13'
$TemplateOIDPath = "CN=OID,CN=Public Key Services,CN=Services,$ConfigNC"
$ESC13Template = "CN=$esc13templateName,CN=Certificate Templates,CN=Public Key Services,CN=Services,$ConfigNC"
$adObject = Get-ADObject -Identity $ESC13Template -Properties msPKI-Certificate-Policy
$group = Get-ADGroup -Identity $esc13group -Properties Members
if ([string]$group.GroupScope -ne 'Universal' -or [string]$group.GroupCategory -ne 'Security' -or @($group.Members).Count -gt 0) {
    throw 'ESC13 requires an empty Universal Security group'
}
$existing = @(Get-ADObject -SearchBase $TemplateOIDPath -SearchScope OneLevel -Filter {DisplayName -eq $IssuanceName} -Properties msPKI-Cert-Template-OID,msDS-OIDToGroupLink,flags)
if ($existing.Count -gt 1) { throw 'Ambiguous ESC13 issuance policies; preserving existing state' }
if ($existing.Count -eq 0) {
    $oid = New-TemplateOID -ConfigNC $ConfigNC
    $existing = @(New-ADObject -Path $TemplateOIDPath -Name $oid.TemplateName -Type 'msPKI-Enterprise-Oid' -OtherAttributes @{
        displayName=$IssuanceName; flags=[int]2; 'msPKI-Cert-Template-OID'=$oid.TemplateOID
    } -PassThru)
    $existing = @(Get-ADObject -Identity $existing[0].DistinguishedName -Properties msPKI-Cert-Template-OID,msDS-OIDToGroupLink,flags)
    $changed = $true
}
$policy = $existing[0]
$oidValue = [string]$policy.'msPKI-Cert-Template-OID'
if ([string]::IsNullOrWhiteSpace($oidValue) -or ([int]$policy.flags -band 2) -eq 0) {
    throw 'Existing ESC13 object is not a valid issuance policy'
}
$link = [string]$policy.'msDS-OIDToGroupLink'
if ($link -and $link -ne $group.DistinguishedName) { throw 'Existing ESC13 policy links another group; preserving it' }
if (-not $link) {
    Set-ADObject -Identity $policy.DistinguishedName -Replace @{'msDS-OIDToGroupLink'=$group.DistinguishedName}
    $changed = $true
}
$policies = @($adObject.'msPKI-Certificate-Policy' | ForEach-Object {[string]$_})
if ($oidValue -notin $policies) {
    Set-ADObject -Identity $adObject.DistinguishedName -Replace @{'msPKI-Certificate-Policy'=[string[]]@($policies + $oidValue)}
    $changed = $true
}
$verifiedPolicy = Get-ADObject -Identity $policy.DistinguishedName -Properties msDS-OIDToGroupLink
$verifiedTemplate = Get-ADObject -Identity $adObject.DistinguishedName -Properties msPKI-Certificate-Policy
if ([string]$verifiedPolicy.'msDS-OIDToGroupLink' -ne $group.DistinguishedName -or $oidValue -notin @($verifiedTemplate.'msPKI-Certificate-Policy')) {
    throw 'ESC13 group/template policy readback failed'
}
if (Get-Variable Ansible -ErrorAction SilentlyContinue) { $Ansible.Changed = $changed }
[pscustomobject]@{Changed=$changed; Template=$esc13templateName; GroupSID=$group.SID.Value; IssuancePolicy=$policy.DistinguishedName}
