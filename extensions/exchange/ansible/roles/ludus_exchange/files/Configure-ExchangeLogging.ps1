$ErrorActionPreference = 'Stop'
Add-PSSnapin Microsoft.Exchange.Management.PowerShell.SnapIn -ErrorAction Stop
$server = Get-ExchangeServer -Identity $env:COMPUTERNAME -ErrorAction Stop
$changed = @()
$adminAudit = Get-AdminAuditLogConfig -ErrorAction Stop
if (-not $adminAudit.AdminAuditLogEnabled) {
    Set-AdminAuditLogConfig -AdminAuditLogEnabled $true -ErrorAction Stop
    $changed += 'Exchange administrator auditing'
}
$transport = Get-TransportService -Identity $server.Name
if (-not $transport.MessageTrackingLogEnabled) {
    Set-TransportService -Identity $server.Name -MessageTrackingLogEnabled $true
    $changed += 'message tracking'
}
if ([string]$transport.IntraOrgConnectorProtocolLoggingLevel -ne 'Verbose') {
    Set-TransportService -Identity $server.Name -IntraOrgConnectorProtocolLoggingLevel Verbose
    $changed += 'intra-organization SMTP'
}
$frontend = Get-FrontEndTransportService -Identity $server.Name
if ([string]$frontend.IntraOrgConnectorProtocolLoggingLevel -ne 'Verbose') {
    Set-FrontEndTransportService -Identity $server.Name -IntraOrgConnectorProtocolLoggingLevel Verbose
    $changed += 'front-end intra-organization SMTP'
}
foreach ($connector in @(Get-ReceiveConnector -Server $server.Name)) {
    if ([string]$connector.ProtocolLoggingLevel -ne 'Verbose') {
        Set-ReceiveConnector -Identity $connector.Identity -ProtocolLoggingLevel Verbose
        $changed += 'SMTP receive: ' + $connector.Name
    }
}
foreach ($connector in @(Get-SendConnector)) {
    if (@($connector.SourceTransportServers | Where-Object {$_.Name -eq $server.Name}).Count -gt 0 -and
        [string]$connector.ProtocolLoggingLevel -ne 'Verbose') {
        Set-SendConnector -Identity $connector.Identity -ProtocolLoggingLevel Verbose
        $changed += 'SMTP send: ' + $connector.Name
    }
}
$mailbox = Get-MailboxTransportService -Identity $server.Name
if ([string]$mailbox.MailboxDeliveryConnectorProtocolLoggingLevel -ne 'Verbose') {
    Set-MailboxTransportService -Identity $server.Name -MailboxDeliveryConnectorProtocolLoggingLevel Verbose
    $changed += 'mailbox delivery SMTP'
}
$channels = @()
foreach ($name in @('MSExchange Management', 'Microsoft-Exchange-ManagedAvailability/Monitoring',
                    'Microsoft-Exchange-ManagedAvailability/RecoveryActionResults')) {
    $channel = Get-WinEvent -ListLog $name -Force -ErrorAction SilentlyContinue
    if (-not $channel) { continue }
    # Do not turn on Analytic/Debug tracing simply to produce data.
    if ([string]$channel.LogType -in @('Analytical','Analytic','Debug')) { continue }
    if (-not $channel.IsEnabled) {
        & wevtutil.exe sl $name /e:true
        if ($LASTEXITCODE -ne 0) { throw "Cannot enable reviewed channel $name" }
        $changed += $name
    }
    $enabled = (Get-WinEvent -ListLog $name -Force -ErrorAction Stop).IsEnabled
    if (-not $enabled) { throw "Exchange event channel readback differs: $name" }
    $channels += @{name=$name;enabled=$enabled;type=[string]$channel.LogType}
}
$transport = Get-TransportService -Identity $server.Name
$mailbox = Get-MailboxTransportService -Identity $server.Name
$frontend = Get-FrontEndTransportService -Identity $server.Name
$receiveConnectors = @(Get-ReceiveConnector -Server $server.Name)
$sendConnectors = @(Get-SendConnector | Where-Object {
    @($_.SourceTransportServers | Where-Object { $_.Name -eq $server.Name }).Count -gt 0
})
if (-not $transport.MessageTrackingLogEnabled -or
    -not (Get-AdminAuditLogConfig -ErrorAction Stop).AdminAuditLogEnabled -or
    [string]$transport.IntraOrgConnectorProtocolLoggingLevel -ne 'Verbose' -or
    [string]$frontend.IntraOrgConnectorProtocolLoggingLevel -ne 'Verbose' -or
    [string]$mailbox.MailboxDeliveryConnectorProtocolLoggingLevel -ne 'Verbose' -or
    @($receiveConnectors | Where-Object { [string]$_.ProtocolLoggingLevel -ne 'Verbose' }).Count -gt 0 -or
    @($sendConnectors | Where-Object { [string]$_.ProtocolLoggingLevel -ne 'Verbose' }).Count -gt 0) {
    throw 'Exchange logging configuration readback differs'
}
$Ansible.Changed = $changed.Count -gt 0
[pscustomobject]@{
    host=$env:COMPUTERNAME;utc=[DateTime]::UtcNow.ToString('o');changed=$changed;
    version=[string]$server.AdminDisplayVersion;channels=$channels;
    admin_audit_enabled=(Get-AdminAuditLogConfig -ErrorAction Stop).AdminAuditLogEnabled;
    message_tracking=@{enabled=$transport.MessageTrackingLogEnabled;path=[string]$transport.MessageTrackingLogPath;max_age=[string]$transport.MessageTrackingLogMaxAge;max_directory=[string]$transport.MessageTrackingLogMaxDirectorySize};
    smtp_levels=@{transport=[string]$transport.IntraOrgConnectorProtocolLoggingLevel;
        frontend=[string]$frontend.IntraOrgConnectorProtocolLoggingLevel;
        mailbox_delivery=[string]$mailbox.MailboxDeliveryConnectorProtocolLoggingLevel};
    smtp_connectors=@{receive=@($receiveConnectors | Select-Object Name,ProtocolLoggingLevel);
        send=@($sendConnectors | Select-Object Name,ProtocolLoggingLevel)};
    smtp_paths=@([string]$transport.SendProtocolLogPath,[string]$transport.ReceiveProtocolLogPath,
        [string]$frontend.SendProtocolLogPath,[string]$frontend.ReceiveProtocolLogPath,
        [string]$mailbox.SendProtocolLogPath,[string]$mailbox.ReceiveProtocolLogPath);
    imap_pop_services=@(Get-Service MSExchangeIMAP4,MSExchangePOP3 -ErrorAction SilentlyContinue |
        Select-Object Name,Status,StartType)
} | ConvertTo-Json -Depth 6 -Compress
