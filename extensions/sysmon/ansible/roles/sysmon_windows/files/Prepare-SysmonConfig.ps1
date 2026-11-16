[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SourcePath,
    [Parameter(Mandatory=$true)][string]$DestinationPath,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [bool]$NetworkConnectAll = $true
)
$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
if ([IO.Path]::GetFullPath($SourcePath) -eq [IO.Path]::GetFullPath($DestinationPath)) {
    throw 'Keep the pinned upstream Sysmon configuration separate from the managed output'
}
if ((Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash -ne $ExpectedSha256) {
    throw 'Pinned upstream Sysmon configuration checksum differs; review before modifying it'
}
$source = [IO.File]::ReadAllText($SourcePath)
$wanted = $source
if ($NetworkConnectAll) {
    # These two whole RuleGroups are the only Event ID 3 filters in the pinned
    # source. Preserve every byte outside them, including the vendor attribution.
    $pattern = '(?s)<RuleGroup name="" groupRelation="or">\s*<NetworkConnect onmatch="(?:include|exclude)">.*?</NetworkConnect>\s*</RuleGroup>'
    $groups = [regex]::Matches($source, $pattern)
    $network = @(([xml]$source).SelectNodes('//NetworkConnect'))
    if ($groups.Count -ne 2 -or $network.Count -ne 2) {
        throw 'Pinned Sysmon network filter layout differs; preserve it for review'
    }
    for ($i = $groups.Count - 1; $i -ge 0; $i--) {
        $replacement = if ($i -eq 0) {
            '<RuleGroup name="SOC Event ID 3 - all network connections" groupRelation="or"><NetworkConnect onmatch="exclude" /></RuleGroup>'
        } else { '' }
        $wanted = $wanted.Remove($groups[$i].Index, $groups[$i].Length).Insert($groups[$i].Index, $replacement)
    }
    $effective = @(([xml]$wanted).SelectNodes('//NetworkConnect'))
    if ($effective.Count -ne 1 -or $effective[0].GetAttribute('onmatch') -ne 'exclude' -or
        @($effective[0].ChildNodes | Where-Object { $_.NodeType -eq [Xml.XmlNodeType]::Element }).Count -ne 0) {
        throw 'The managed Sysmon network filter is not collect-all Event ID 3'
    }
}
$changed = -not (Test-Path -LiteralPath $DestinationPath)
if (-not $changed) { $changed = [IO.File]::ReadAllText($DestinationPath) -cne $wanted }
if ($changed) {
    $temporary = $DestinationPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temporary, $wanted, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $DestinationPath) {
            [IO.File]::Replace($temporary, $DestinationPath, $null)
        } else {
            [IO.File]::Move($temporary, $DestinationPath)
        }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -ErrorAction Stop }
    }
}
$Ansible.Changed = $changed
$Ansible.Result = @{network_connect_all=$NetworkConnectAll;configuration_sha256=(Get-FileHash -LiteralPath $DestinationPath -Algorithm SHA256).Hash}
