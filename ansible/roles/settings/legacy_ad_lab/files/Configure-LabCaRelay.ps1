# Operator-selected PetitPotam -> AD CS lab relay conditions; not a mitigation.
$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
Set-StrictMode -Version 2.0
Import-Module WebAdministration -ErrorAction Stop
if (-not (Get-Service CertSvc -ErrorAction SilentlyContinue)) { throw 'No local CA for the selected relay lab' }
if (-not (Get-WebApplication -Site 'Default Web Site' -Name 'certsrv')) {
    throw 'CA Web Enrollment application is missing; native AD CS installation must complete first'
}
$site = 'Default Web Site'
$location = "$site/certsrv"
$store = 'MACHINE/WEBROOT/APPHOST'
$auth = 'system.webServer/security/authentication/windowsAuthentication'
$anonymous = 'system.webServer/security/authentication/anonymousAuthentication'
# WebAdministration returns either an attribute wrapper or its scalar value.
function Get-RelayIisValue([string]$Filter, [string]$Name) {
    $properties = @(Get-WebConfigurationProperty -PSPath $store -Location $location -Filter $Filter -Name $Name)
    if ($Name -ne 'value' -and $properties.Count -ne 1) {
        throw "Expected one IIS value for $Filter/$Name; got $($properties.Count)"
    }
    foreach ($property in $properties) {
        if ($null -eq $property -and $Name -eq 'value') { continue }
        if ($null -eq $property) { throw "Missing IIS value for $Filter/$Name" }
        $attribute = $property.PSObject.Properties['Value']
        $value = if ($null -ne $attribute) { $attribute.Value } else { $property }
        if ($null -eq $value) { throw "Null IIS value for $Filter/$Name" }
        $value
    }
}
function Test-RelayIisNone($Value) {
    # IIS tokenChecking=None and access.sslFlags=None both have numeric value 0.
    [string]$Value -in @('None', '0')
}
$http = @(Get-WebBinding -Name $site -Protocol http)
if ($http.Count -eq 0) { throw 'Existing CA site has no HTTP binding; no new listener will be invented' }
$anonymousEnabled = [Convert]::ToBoolean((Get-RelayIisValue $anonymous enabled))
$enabled = [Convert]::ToBoolean((Get-RelayIisValue $auth enabled))
$providers = @(Get-RelayIisValue "$auth/providers/add" value)
$protection = Get-RelayIisValue "$auth/extendedProtection" tokenChecking
$ssl = Get-RelayIisValue 'system.webServer/security/access' sslFlags
if ($anonymousEnabled) {
    Set-WebConfigurationProperty -PSPath $store -Location $location -Filter $anonymous -Name enabled -Value $false
    $Ansible.Changed = $true
}
if (-not $enabled) {
    Set-WebConfigurationProperty -PSPath $store -Location $location -Filter $auth -Name enabled -Value $true
    $Ansible.Changed = $true
}
if ('NTLM' -notin $providers) {
    Add-WebConfigurationProperty -PSPath $store -Location $location -Filter "$auth/providers" -Name '.' -Value @{value='NTLM'}
    $Ansible.Changed = $true
}
if (-not (Test-RelayIisNone $protection)) {
    Set-WebConfigurationProperty -PSPath $store -Location $location -Filter "$auth/extendedProtection" -Name tokenChecking -Value 'None'
    $Ansible.Changed = $true
}
if (-not (Test-RelayIisNone $ssl)) {
    Set-WebConfigurationProperty -PSPath $store -Location $location -Filter 'system.webServer/security/access' -Name sslFlags -Value 'None'
    $Ansible.Changed = $true
}
$anonymousEnabled = [Convert]::ToBoolean((Get-RelayIisValue $anonymous enabled))
$enabled = [Convert]::ToBoolean((Get-RelayIisValue $auth enabled))
$providers = @(Get-RelayIisValue "$auth/providers/add" value)
$protection = Get-RelayIisValue "$auth/extendedProtection" tokenChecking
$ssl = Get-RelayIisValue 'system.webServer/security/access' sslFlags
if (-not $enabled) { throw 'AD CS relay readback: Windows authentication is disabled' }
if ($anonymousEnabled) { throw 'AD CS relay readback: anonymous authentication is enabled' }
if ('NTLM' -notin $providers) { throw 'AD CS relay readback: NTLM provider is missing' }
if (-not (Test-RelayIisNone $protection)) { throw "AD CS relay readback: tokenChecking=$protection; expected None (0)" }
if (-not (Test-RelayIisNone $ssl)) { throw "AD CS relay readback: sslFlags=$ssl; expected None (0)" }
[pscustomobject]@{application=$location;windows_auth=$true;ntlm=$true;epa='None';require_ssl=$false;
    scope='isolated lab';coercion_tested=$false;relay_tested=$false}
