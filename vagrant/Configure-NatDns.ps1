#Requires -RunAsAdministrator

param(
    [ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')]
    [string]$AdditionalDnsServer = '1.1.1.1'
)

$ErrorActionPreference = 'Stop'

# The libvirt management/NAT adapter is the only physical adapter whose IPv4
# address comes from DHCP. Selecting it this way avoids changing DNS on the
# static AD lab and SOC monitoring adapters.
$natAddress = Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp -ErrorAction Stop |
    Where-Object {
        $_.IPAddress -notlike '169.254.*' -and
        (Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue).Status -eq 'Up'
    } |
    Select-Object -First 1

if (-not $natAddress) {
    throw 'Cannot identify the active DHCP/NAT adapter'
}

$adapter = Get-NetAdapter -InterfaceIndex $natAddress.InterfaceIndex -ErrorAction Stop
$currentServers = @(
    (Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction Stop).ServerAddresses |
        Where-Object { $_ }
)
$desiredServers = @($currentServers + $AdditionalDnsServer | Select-Object -Unique)

Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses $desiredServers

$configuredServers = @(
    (Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction Stop).ServerAddresses
)
if ($AdditionalDnsServer -notin $configuredServers) {
    throw "Additional DNS server $AdditionalDnsServer was not applied to $($adapter.Name)"
}

Write-Host "NAT adapter $($adapter.Name) DNS servers: $($configuredServers -join ', ')"
