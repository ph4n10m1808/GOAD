$Ansible.Changed = $false
$root = Join-Path $env:TEMP ('ssms-diagnostics-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
# Keep complete logs: the finalizer error is often outside the last 80 lines.
$sources = @($env:TEMP, 'C:\Windows\Temp') | Select-Object -Unique
$index = 0
foreach ($source in $sources) {
    $dest = Join-Path $root "logs-$index"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Get-ChildItem -LiteralPath $source -Filter 'dd_*' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-2) } |
        Copy-Item -Destination $dest -ErrorAction Continue
    $index++
}
$os = Get-CimInstance Win32_OperatingSystem
$memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$system = [ordered]@{
    CapturedAt = (Get-Date).ToString('o')
    OS = $os | Select-Object Caption, Version, BuildNumber, TotalVisibleMemorySize, FreePhysicalMemory, TotalVirtualMemorySize, FreeVirtualMemory
    Memory = $memory | Select-Object AvailableMBytes, CommittedBytes, CommitLimit
    PageFile = @(Get-CimInstance Win32_PageFileUsage | Select-Object Name, AllocatedBaseSize, CurrentUsage, PeakUsage)
    DotNet = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' | Select-Object Release, Version
    WinRMMemoryLimit = (Get-Item WSMan:\localhost\Shell\MaxMemoryPerShellMB -ErrorAction SilentlyContinue).Value
    Disk = @(Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" | Select-Object Size, FreeSpace)
}
$system | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $root 'system.json') -Encoding UTF8
Get-WinEvent -FilterHashtable @{
    LogName = 'Application'; Id = @(1000, 1001, 1026); StartTime = (Get-Date).AddDays(-2)
} -ErrorAction SilentlyContinue | Select-Object TimeCreated, Id, ProviderName, Message |
    ConvertTo-Json -Depth 4 | Set-Content (Join-Path $root 'application-errors.json') -Encoding UTF8
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path -LiteralPath $vswhere) {
    & $vswhere -all -products Microsoft.VisualStudio.Product.Ssms -format json -utf8 |
        Set-Content (Join-Path $root 'registration.json') -Encoding UTF8
}
$archive = "$root.zip"
Compress-Archive -Path "$root\*" -DestinationPath $archive -Force
$Ansible.Result = @{ archive = $archive }
Write-Output "Full SSMS diagnostics: $archive"
