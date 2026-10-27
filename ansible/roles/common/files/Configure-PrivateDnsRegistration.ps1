param([Parameter(Mandatory=$true)][string]$PrivateIPAddress)
$ErrorActionPreference = 'Stop'
$private = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $PrivateIPAddress)
if ($private.Count -ne 1) { throw 'Expected exactly one domain-network address' }
$changed = @()
foreach ($client in Get-DnsClient) {
    if ($client.InterfaceAlias -like 'Loopback*') { continue }
    $wanted = $client.InterfaceIndex -eq $private[0].InterfaceIndex
    if ($client.RegisterThisConnectionsAddress -ne $wanted) {
        Set-DnsClient -InterfaceIndex $client.InterfaceIndex -RegisterThisConnectionsAddress $wanted
        $changed += $client.InterfaceAlias
    }
}
$removed = @()
if ((Get-Service DNS -ErrorAction SilentlyContinue).Status -eq 'Running') {
    $settings = Get-DnsServerSetting -All
    $listen = @($settings.ListeningIPAddress | ForEach-Object { $_.ToString() })
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\DNS\Parameters'
    $publishAddress = (Get-ItemProperty $key -Name PublishAddresses -ErrorAction SilentlyContinue).PublishAddresses
    if ($listen.Count -ne 1 -or $listen[0] -ne $PrivateIPAddress -or $publishAddress -ne $PrivateIPAddress) {
        # Preserve this machine's native DNS configuration before either write.
        $backupName = 'dns-before-soc-{0}-{1}-{2}' -f $env:COMPUTERNAME,
            [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'), [Guid]::NewGuid().ToString('N')
        $backupDirectory = Join-Path $env:ProgramData $backupName
        New-Item -ItemType Directory -Path $backupDirectory | Out-Null
        $acl = Get-Acl -LiteralPath $backupDirectory
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                (New-Object System.Security.Principal.SecurityIdentifier($sid)),
                'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $backupDirectory -AclObject $acl
        $registryBackup = Join-Path $backupDirectory 'dns-service.reg'
        & reg.exe export 'HKLM\SYSTEM\CurrentControlSet\Services\DNS' $registryBackup | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $registryBackup) -or
            (Get-Item -LiteralPath $registryBackup).Length -eq 0) {
            throw 'Cannot preserve the native DNS service registry configuration'
        }
    }
    if ($listen.Count -ne 1 -or $listen[0] -ne $PrivateIPAddress) {
        $settings.ListeningIPAddress = @($PrivateIPAddress)
        Set-DnsServerSetting -InputObject $settings
        $actualListen = @((Get-DnsServerSetting -All).ListeningIPAddress | ForEach-Object { $_.ToString() })
        if ($actualListen.Count -ne 1 -or $actualListen[0] -ne $PrivateIPAddress) {
            throw 'DNS listening address readback differs'
        }
        $changed += 'DNS listening address'
    }
    # Set only this value in the existing DNS service key; never recreate system keys.
    if ($publishAddress -ne $PrivateIPAddress) {
        Set-ItemProperty $key -Name PublishAddresses -Type String -Value $PrivateIPAddress
        if ((Get-ItemProperty $key -Name PublishAddresses).PublishAddresses -ne $PrivateIPAddress) {
            throw 'DNS published address readback differs'
        }
        $changed += 'DNS published address'
    }
    $domain = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().DomainName
    $hostName = $env:COMPUTERNAME.ToLowerInvariant()
    $records = @(Get-DnsServerResourceRecord -ZoneName $domain -Name $hostName -RRType A)
    if (-not @($records | Where-Object { $_.RecordData.IPv4Address.ToString() -eq $PrivateIPAddress }).Count) {
        throw 'Private A record is missing; retain existing addresses until private registration is verified'
    }
    $stale = @($records | Where-Object { $_.RecordData.IPv4Address.ToString() -ne $PrivateIPAddress })
    if ($stale.Count) {
        $backup = 'C:\ProgramData\Elastic\dns-host-records-before-soc.json'
        if (-not (Test-Path (Split-Path $backup))) { New-Item -ItemType Directory -Path (Split-Path $backup) | Out-Null }
        if (-not (Test-Path $backup)) {
            @($records | ForEach-Object { [pscustomobject]@{zone=$domain;name=$hostName;ip=$_.RecordData.IPv4Address.ToString();ttl=$_.TimeToLive.TotalSeconds} }) |
                ConvertTo-Json | Set-Content -Path $backup -Encoding UTF8
        }
        foreach ($record in $stale) {
            Remove-DnsServerResourceRecord -ZoneName $domain -InputObject $record -Force
            $removed += $record.RecordData.IPv4Address.ToString()
        }
    }
    & nltest.exe /dsregdns | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Domain controller DNS registration failed' }
}
& ipconfig.exe /registerdns | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Host DNS registration failed' }
Clear-DnsClientCache
[pscustomobject]@{host=$env:COMPUTERNAME;private_address=$PrivateIPAddress;changed=$changed;removed_host_addresses=$removed} | ConvertTo-Json -Compress
