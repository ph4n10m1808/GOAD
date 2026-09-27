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

    [ValidatePattern('^$|^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$')]
    [string]$SocNetworkPrefix = "192.168.50.0/24",

    [ValidatePattern('^$|^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')]
    [string]$ExcludedMacAddress = ""
)

$ErrorActionPreference = "Stop"
$stateDirectory = "C:\ProgramData\GOAD"
$stateFile = Join-Path $stateDirectory "virtio-private-network-ready"

function Get-TargetAdapter {
    $normalizedExcludedMac = $ExcludedMacAddress.Replace(':', '-').ToUpperInvariant()
    $matchingAddress = Get-NetIPAddress `
        -AddressFamily IPv4 `
        -IPAddress $IPAddress `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1

    if ($matchingAddress) {
        $adapter = Get-NetAdapter -InterfaceIndex $matchingAddress.InterfaceIndex -ErrorAction Stop
        if ($adapter.InterfaceDescription -match "VirtIO|Red Hat" -and
            (-not $normalizedExcludedMac -or $adapter.MacAddress.ToUpperInvariant() -ne $normalizedExcludedMac)) {
            return $adapter
        }
    }

    # During this phase the private adapter is the VirtIO adapter without a
    # DHCP address. The management adapter remains the WinRM rescue path.
    return Get-NetAdapter -Physical -ErrorAction Stop |
        Where-Object {
            $_.Status -eq "Up" -and
            $_.InterfaceDescription -match "VirtIO|Red Hat" -and
            (-not $normalizedExcludedMac -or $_.MacAddress.ToUpperInvariant() -ne $normalizedExcludedMac) -and
            -not (Get-NetIPAddress -InterfaceIndex $_.ifIndex `
                -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.PrefixOrigin -eq "Dhcp" })
        } |
        Select-Object -First 1
}

$adapter = Get-TargetAdapter
if (-not $adapter) {
    throw "Cannot identify the private VirtIO network adapter for $IPAddress"
}

$currentAddress = Get-NetIPAddress `
    -InterfaceIndex $adapter.ifIndex `
    -AddressFamily IPv4 `
    -IPAddress $IPAddress `
    -ErrorAction SilentlyContinue

if (-not $currentAddress) {
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "169.254.*" } |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue

    Set-NetIPInterface `
        -InterfaceIndex $adapter.ifIndex `
        -AddressFamily IPv4 `
        -Dhcp Disabled
    New-NetIPAddress `
        -InterfaceIndex $adapter.ifIndex `
        -IPAddress $IPAddress `
        -PrefixLength $PrefixLength |
        Out-Null
}

$verifiedAddress = Get-NetIPAddress `
    -InterfaceIndex $adapter.ifIndex `
    -AddressFamily IPv4 `
    -IPAddress $IPAddress `
    -ErrorAction SilentlyContinue
if (-not $verifiedAddress) {
    throw "Static lab address $IPAddress/$PrefixLength was not applied to the VirtIO adapter"
}

# Keep the private gateway available as a high-metric fallback. The NAT NIC
# remains the preferred Internet default route; the more-specific SOC route
# below uses the private bridge without changing that default.
$defaultGatewayMetric = 1000
function Set-PersistentRoute {
    param(
        [string]$Prefix,
        [string]$Destination,
        [string]$Mask,
        [int]$Metric
    )

    $routePattern = "(?m)^\s*$([regex]::Escape($Destination))\s+" +
        "$([regex]::Escape($Mask))\s+$([regex]::Escape($GatewayAddress))\s+(\d+)\s*$"

    function Get-PersistentRouteMetric {
        $routeOutput = (& route.exe PRINT -4 | Out-String)
        if ($LASTEXITCODE -ne 0) {
            throw "Could not inspect persistent routes for $Destination"
        }
        $persistentTable = $routeOutput -split 'Persistent Routes:', 2
        if ($persistentTable.Count -ne 2) {
            throw "Could not read the persistent route table for $Destination"
        }
        $match = [regex]::Match($persistentTable[1], $routePattern)
        if ($match.Success) { return [int]$match.Groups[1].Value }
        return $null
    }

    $existingMetric = Get-PersistentRouteMetric
    if ($null -ne $existingMetric -and $existingMetric -ne $Metric) {
        & route.exe DELETE $Destination MASK $Mask $GatewayAddress | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not replace persistent route $Destination through $GatewayAddress"
        }
        $existingMetric = $null
    }

    if ($null -eq $existingMetric) {
        & route.exe -p ADD $Destination MASK $Mask $GatewayAddress `
            METRIC $Metric IF $adapter.ifIndex | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not persist route $Destination through $GatewayAddress"
        }
    }

    if ((Get-PersistentRouteMetric) -ne $Metric) {
        throw "Persistent route $Destination through $GatewayAddress failed verification"
    }
}

Set-PersistentRoute -Prefix "0.0.0.0/0" -Destination "0.0.0.0" -Mask "0.0.0.0" -Metric $defaultGatewayMetric
if ($SocNetworkPrefix) {
    $socParts = $SocNetworkPrefix.Split('/')
    $socMaskBits = [int]$socParts[1]
    if ($socMaskBits -lt 1 -or $socMaskBits -gt 32) {
        throw "Invalid SOC network prefix length: $socMaskBits"
    }
    $socMaskBytes = [byte[]]@(0, 0, 0, 0)
    for ($bit = 0; $bit -lt $socMaskBits; $bit++) {
        $octet = [int][Math]::Floor($bit / 8)
        $shift = 7 - ($bit % 8)
        $socMaskBytes[$octet] = [byte]($socMaskBytes[$octet] -bor (1 -shl $shift))
    }
    $socMask = ([System.Net.IPAddress]::new($socMaskBytes)).ToString()
    Set-PersistentRoute -Prefix $SocNetworkPrefix -Destination $socParts[0] -Mask $socMask -Metric 5
}

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
Set-Content -Path $stateFile -Value "$IPAddress/$PrefixLength" -Encoding Ascii
Write-Host "Private VirtIO adapter $($adapter.Name) is ready at $IPAddress/$PrefixLength; gateway $GatewayAddress"
