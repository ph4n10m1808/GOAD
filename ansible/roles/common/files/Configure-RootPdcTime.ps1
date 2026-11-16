param([Parameter(Mandatory=$true)][string[]]$PeerHosts)

$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
Import-Module ActiveDirectory -ErrorAction Stop
$domain = Get-ADDomain
$forest = Get-ADForest
$fqdn = $env:COMPUTERNAME + '.' + $domain.DNSRoot
if ($domain.DNSRoot -ne $forest.RootDomain -or $domain.PDCEmulator -ine $fqdn) {
    throw 'External NTP configuration is restricted to the forest-root PDC emulator'
}
if ($PeerHosts.Count -lt 3 -or $PeerHosts.Count -gt 4 -or
    @($PeerHosts | Select-Object -Unique).Count -ne $PeerHosts.Count) {
    throw 'Configure three or four distinct NTP hosts'
}
foreach ($peer in $PeerHosts) {
    if ($peer -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$') {
        throw 'Invalid NTP host; flags are supplied by this script'
    }
}
$desiredPeers = ($PeerHosts | ForEach-Object { $_ + ',0x8' }) -join ' '
$policy = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\W32Time\Parameters' -ErrorAction SilentlyContinue
if ($policy -and (($policy.Type -and $policy.Type -ne 'NTP') -or
    ($policy.NtpServer -and $policy.NtpServer -ne $desiredPeers))) {
    throw 'Conflicting Windows Time Group Policy; review its owner before changing local configuration'
}
if ((Get-TimeZone).Id -ne 'SE Asia Standard Time') {
    Set-TimeZone -Id 'SE Asia Standard Time'
    $Ansible.Changed = $true
}
$zone = Get-TimeZone
if ($zone.BaseUtcOffset.TotalMinutes -ne 420 -or $zone.SupportsDaylightSavingTime) {
    throw 'Expected fixed UTC+7 without daylight saving time'
}
$service = Get-Service W32Time
if ($service.StartType -ne 'Automatic') {
    Set-Service W32Time -StartupType Automatic
    $Ansible.Changed = $true
}
if ($service.Status -ne 'Running') {
    Start-Service W32Time
    $Ansible.Changed = $true
}
$parameters = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters'
$config = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config'
if ($parameters.Type -ne 'NTP' -or $parameters.NtpServer -ne $desiredPeers -or $config.AnnounceFlags -ne 5) {
    & w32tm.exe /config "/manualpeerlist:$desiredPeers" /syncfromflags:manual /reliable:yes /update | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Native PDC NTP configuration failed' }
    $Ansible.Changed = $true
}
$source = ''
for ($attempt = 0; $attempt -lt 6; $attempt++) {
    & w32tm.exe /resync /rediscover | Out-Null
    $syncExit = $LASTEXITCODE
    $source = (& w32tm.exe /query /source | Out-String).Trim()
    $sourceHost = ($source -split ',')[0]
    if ($syncExit -eq 0 -and $sourceHost -in $PeerHosts) { break }
    if ($attempt -lt 5) { Start-Sleep -Seconds 5 }
}
if ($syncExit -ne 0 -or $sourceHost -notin $PeerHosts) {
    throw 'External NTP synchronization not verified; retain manual peers and inspect W32Time logs'
}
$parameters = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters'
if ($parameters.Type -ne 'NTP' -or $parameters.NtpServer -ne $desiredPeers) {
    throw 'PDC NTP configuration readback differs'
}
$Ansible.Result = @{
    host=$env:COMPUTERNAME; domain=$domain.DNSRoot; pdc=$domain.PDCEmulator;
    timezone=$zone.Id; utc_offset_minutes=420; daylight_saving=$false;
    type=$parameters.Type; peers=$desiredPeers; source=$source;
    status=(& w32tm.exe /query /status /verbose | Out-String).Trim();
    utc=[DateTime]::UtcNow.ToString('o'); sync_verified=$true
}
