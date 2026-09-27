#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"
$stateDirectory = "C:\ProgramData\GOAD"
$stateFile = Join-Path $stateDirectory "virtio-storage-ready"
$installLog = "C:\Windows\Temp\virtio-install.log"
$successCodes = @(0, 1641, 3010)

function Test-VirtIOHealth {
    $storageDevice = Get-CimInstance Win32_DiskDrive | Where-Object {
        $_.PNPDeviceID -match "VEN_1AF4|VIRTIO" -or
        $_.Model -match "VirtIO|Red Hat"
    } | Select-Object -First 1

    $viostor = Get-Service -Name "viostor" -ErrorAction SilentlyContinue
    $netKvm = Get-WindowsDriver -Online -All | Where-Object {
        $_.OriginalFileName -match "netkvm\.inf$"
    } | Select-Object -First 1

    return ($null -ne $storageDevice -and $null -ne $viostor -and $null -ne $netKvm)
}

if ((Test-Path $stateFile) -and (Test-VirtIOHealth)) {
    Write-Host "VirtIO drivers are already installed and healthy"
    exit 0
}

$isoRoot = $null
foreach ($letter in [char[]](68..90)) {
    $candidate = "${letter}:\"
    if ((Test-Path (Join-Path $candidate "virtio-win-guest-tools.exe")) -or
        (Test-Path (Join-Path $candidate "virtio-win-gt-x64.msi"))) {
        $isoRoot = $candidate
        break
    }
}

if (-not $isoRoot) {
    throw "VirtIO ISO is not mounted or does not contain a supported installer"
}

$installer = Join-Path $isoRoot "virtio-win-guest-tools.exe"
$msi = Join-Path $isoRoot "virtio-win-gt-x64.msi"

if (Test-Path $installer) {
    $process = Start-Process `
        -FilePath $installer `
        -ArgumentList @("/install", "/quiet", "/norestart", "ACCEPTEULA=1") `
        -Wait `
        -PassThru

    if ($process.ExitCode -notin $successCodes) {
        throw "VirtIO Guest Tools failed with exit code $($process.ExitCode)"
    }
} elseif (Test-Path $msi) {
    $arguments = @(
        "/i", "`"$msi`"", "/qn", "/norestart",
        "/l*v", "`"$installLog`""
    )
    $process = Start-Process `
        -FilePath "msiexec.exe" `
        -ArgumentList $arguments `
        -Wait `
        -PassThru

    if ($process.ExitCode -notin $successCodes) {
        throw "VirtIO MSI failed with exit code $($process.ExitCode); see $installLog"
    }
} else {
    throw "No VirtIO installer found on $isoRoot"
}

if (-not (Test-VirtIOHealth)) {
    throw "VirtIO installer completed, but storage/viostor/NetKVM health checks failed"
}

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
New-Item -ItemType File -Path $stateFile -Force | Out-Null
Write-Host "VirtIO storage and network drivers are ready"
