#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"
$timeZoneId = "SE Asia Standard Time"

if ((Get-TimeZone).Id -ne $timeZoneId) {
    Set-TimeZone -Id $timeZoneId
}

Set-Service -Name W32Time -StartupType Automatic
if ((Get-Service -Name W32Time).Status -ne "Running") {
    Start-Service -Name W32Time
}

# Domain members/controllers use their normal Windows time hierarchy. A
# resync is best-effort because a new domain may not have a source yet.
w32tm.exe /resync /nowait | Write-Host
if ($LASTEXITCODE -ne 0) {
    Write-Warning "W32Time resync is pending; Windows will retry automatically"
}

Write-Host "Timezone: $((Get-TimeZone).Id); local time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')"
