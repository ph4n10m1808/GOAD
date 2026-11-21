$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false

$role = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\ExchangeServer\v15\MailboxRole'
if ([string]::IsNullOrWhiteSpace([string]$role.ConfiguredVersion) -or
    [string]$role.ConfiguredVersion -ne [string]$role.UnpackedVersion) {
    throw 'Complete Exchange Mailbox Setup before repairing web assembly configuration'
}

$root = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\ExchangeServer\v15\Setup').MsiInstallPath
$bin = Join-Path $root 'Bin'
$generator = Join-Path $bin 'DependentAssemblyGenerator.exe'
$pendingReload = Join-Path $bin '.goad-web-assembly-reload.pending'

function Test-AssemblyConfig([string]$Path) {
    $config = [xml](Get-Content -LiteralPath $Path -Raw)
    if ($config.SelectNodes("//*[local-name()='dependentAssembly']").Count -eq 0) {
        throw "No assembly dependencies in $Path"
    }
}

Push-Location -LiteralPath $bin
try {
    foreach ($component in @('ClientAccess', 'FrontEnd\HttpProxy')) {
        $directory = Join-Path $root $component
        $target = Join-Path $directory 'SharedWebConfig.config'
        if (Test-Path -LiteralPath $target) {
            Test-AssemblyConfig $target
            continue
        }

        # Generate beside the target and publish only a complete XML file.
        $staged = Join-Path $directory ('.assembly-' + [guid]::NewGuid().ToString('N') + '.config')
        try {
            & $generator -exchangePath $bin -exchangePath $directory -configFile $staged | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Exchange assembly generator exited with code $LASTEXITCODE for $component"
            }
            Test-AssemblyConfig $staged
            # Persist activation intent before publishing the first file.
            [IO.File]::WriteAllText($pendingReload, 'IIS reload pending')
            try {
                [IO.File]::Move($staged, $target)
                $Ansible.Changed = $true
            } catch [IO.IOException] {
                if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw }
                # A concurrent repair published the target first. Validate it in place.
                Test-AssemblyConfig $target
            }
        } finally {
            if (Test-Path -LiteralPath $staged -PathType Leaf) {
                Remove-Item -LiteralPath $staged
            }
        }
    }
} finally {
    Pop-Location
}

if (Test-Path -LiteralPath $pendingReload -PathType Leaf) {
    & "$env:windir\System32\iisreset.exe" /restart
    if ($LASTEXITCODE -ne 0) { throw "IIS restart exited with code $LASTEXITCODE" }
    $Ansible.Changed = $true
    $Ansible.Result = @{pending_reload = $pendingReload}
}
