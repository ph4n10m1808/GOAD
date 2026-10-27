$ErrorActionPreference = 'Stop'
Import-Module WebAdministration
# IIS 1.24.9's native access pipeline supports this W3C field layout.
$required = @('Date','Time','SiteName','ServerIP','Method','UriStem','UriQuery',
              'ServerPort','UserName','ClientIP','ProtocolVersion','UserAgent',
              'Cookie','Referer','HttpStatus','HttpSubStatus','Win32Status',
              'BytesSent','BytesRecv','TimeTaken')
$targets = @('system.applicationHost/sites/siteDefaults/logFile')
foreach ($site in Get-Website) {
    if ($site.Name.Contains("'")) { throw 'Review IIS site name before updating logging' }
    $targets += "system.applicationHost/sites/site[@name='$($site.Name)']/logFile"
}
$plans = @()
foreach ($filter in $targets) {
    $property = Get-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
        -Filter $filter -Name logExtFileFlags
    if ($null -ne $property.Value) { $property = $property.Value }
    $value = $property.ToString()
    $existing = @($value.Split(',') | ForEach-Object {$_.Trim()})
    $missing = @($required | Where-Object {$_ -notin $existing})
    if ($missing.Count) {
        $plans += [pscustomobject]@{filter=$filter; value=(($existing + $missing) -join ',')}
    }
}
if ($plans.Count) {
    $backup = 'SOC-Before-HTTP-Version-Logging'
    if (-not (Test-Path "$env:windir\System32\inetsrv\backup\$backup")) {
        Backup-WebConfiguration -Name $backup | Out-Null
    }
    foreach ($plan in $plans) {
        Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
            -Filter $plan.filter -Name logExtFileFlags -Value $plan.value
    }
}
foreach ($filter in $targets) {
    $property = Get-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
        -Filter $filter -Name logExtFileFlags
    if ($null -ne $property.Value) { $property = $property.Value }
    $actual = $property.ToString().Split(',') | ForEach-Object {$_.Trim()}
    if (@($required | Where-Object {$_ -notin $actual}).Count) {
        throw 'IIS W3C logging field readback differs'
    }
}
$receipt = "$env:ProgramData\Elastic\soc-iis-logging-accepted.json"
if (-not (Test-Path $receipt)) {
    # Real, bounded local requests seed both enabled log sources on a fresh lab.
    # This is a collection readiness check, not a simulated attack or fake event.
    $normal = Invoke-WebRequest -UseBasicParsing `
        -Uri 'http://127.0.0.1/?soc_iis_version_acceptance=1' -TimeoutSec 10
    if ($normal.StatusCode -ne 200) { throw 'IIS access readiness request failed' }
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.ReceiveTimeout = 10000
        $client.SendTimeout = 10000
        $client.Connect('127.0.0.1', 80)
        $stream = $client.GetStream()
        $request = [Text.Encoding]::ASCII.GetBytes(
            "GET /soc-http-monitoring-acceptance HTTP/1.1`r`nHost: localhost`r`nBad Header: acceptance`r`nConnection: close`r`n`r`n")
        $stream.Write($request, 0, $request.Length)
        $response = New-Object byte[] 256
        $count = $stream.Read($response, 0, $response.Length)
        if ([Text.Encoding]::ASCII.GetString($response,0,$count) -notmatch '^HTTP/1\.[01] 400 ') {
            throw 'IIS HTTPERR readiness request did not return the expected 400'
        }
    } finally { $client.Dispose() }
    $directory = Split-Path $receipt
    if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory | Out-Null }
    @{utc=[DateTime]::UtcNow.ToString('o'); access_status=200; httperr_status=400} |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $receipt -Encoding UTF8
}
@{changed=($plans.Count -gt 0); targets=$targets.Count; fields=$required;
  backup='SOC-Before-HTTP-Version-Logging'} | ConvertTo-Json -Compress
