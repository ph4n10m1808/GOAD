#Requires -RunAsAdministrator

param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')]
    [string]$IPAddress,

    [ValidateRange(1, 32)]
    [int]$PrefixLength = 24,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')]
    [string]$GatewayAddress,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')]
    [string]$LegacyGatewayAddress,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')]
    [string]$MacAddress
)

$ErrorActionPreference = 'Stop'

function Get-SocAdapter {
    $normalizedMac = $MacAddress.Replace(':', '-').ToUpperInvariant()
    $matchingAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $IPAddress -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($matchingAddress) {
        $adapter = Get-NetAdapter -InterfaceIndex $matchingAddress.InterfaceIndex -ErrorAction Stop
        if ($adapter.MacAddress.ToUpperInvariant() -eq $normalizedMac) {
            return $adapter
        }
    }

    return Get-NetAdapter -Physical -ErrorAction Stop |
        Where-Object {
            $_.Status -eq 'Up' -and
            $_.InterfaceDescription -match 'VirtIO|Red Hat' -and
            $_.MacAddress.ToUpperInvariant() -eq $normalizedMac
        } |
        Select-Object -First 1
}

$adapter = Get-SocAdapter
if (-not $adapter) {
    throw "Cannot identify SOC VirtIO adapter $MacAddress for $IPAddress"
}

if (-not (Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -IPAddress $IPAddress -ErrorAction SilentlyContinue)) {
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like '169.254.*' } |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Disabled
    New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $IPAddress -PrefixLength $PrefixLength | Out-Null
}

# The SOC network is monitoring-only. Prevent this address from being
# published in AD DNS even when a later DC task refreshes DNS registrations.
Set-DnsClient -InterfaceIndex $adapter.ifIndex `
    -RegisterThisConnectionsAddress $false `
    -UseSuffixWhenRegistering $false
Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ResetServerAddresses

# Every NIC has a usable gateway, but the monitoring NIC must never take
# precedence over the NAT or AD private adapters. Their fallback metrics are
# lower (DHCP/NAT first, private lab NIC at 1000, SOC NIC at 2000).
$socGatewayMetric = 2000
$destination = '0.0.0.0'
$mask = '0.0.0.0'
$routePattern = "(?m)^\s*$([regex]::Escape($destination))\s+" +
    "$([regex]::Escape($mask))\s+$([regex]::Escape($GatewayAddress))\s+(\d+)\s*$"

function Get-SocPersistentGatewayMetric {
    $routeOutput = (& route.exe PRINT -4 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the persistent SOC gateway through $GatewayAddress"
    }
    $persistentTable = $routeOutput -split 'Persistent Routes:', 2
    if ($persistentTable.Count -ne 2) {
        throw "Could not read the persistent SOC gateway table"
    }
    $match = [regex]::Match($persistentTable[1], $routePattern)
    if ($match.Success) { return [int]$match.Groups[1].Value }
    return $null
}

$existingGatewayMetric = Get-SocPersistentGatewayMetric
if ($null -ne $existingGatewayMetric -and $existingGatewayMetric -ne $socGatewayMetric) {
    & route.exe DELETE $destination MASK $mask $GatewayAddress | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not replace the persistent SOC gateway through $GatewayAddress"
    }
    $existingGatewayMetric = $null
}
if ($null -eq $existingGatewayMetric) {
    & route.exe -p ADD $destination MASK $mask $GatewayAddress `
        METRIC $socGatewayMetric IF $adapter.ifIndex | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not persist the SOC gateway through $GatewayAddress"
    }
}
if ((Get-SocPersistentGatewayMetric) -ne $socGatewayMetric) {
    throw "Persistent SOC gateway through $GatewayAddress failed verification"
}

# Remove the legacy host route installed before GOAD-Light had a directly
# connected SOC NIC. The on-link 192.168.50.0/24 route now wins naturally.
& route.exe DELETE 192.168.50.10 MASK 255.255.255.255 $LegacyGatewayAddress | Out-Null

$configuredAddress = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -IPAddress $IPAddress -ErrorAction SilentlyContinue
if (-not $configuredAddress) {
    throw "SOC address $IPAddress/$PrefixLength was not applied to $($adapter.Name)"
}

Write-Host "SOC VirtIO adapter $($adapter.Name) is ready at $IPAddress/$PrefixLength; gateway $GatewayAddress (metric $socGatewayMetric)"
