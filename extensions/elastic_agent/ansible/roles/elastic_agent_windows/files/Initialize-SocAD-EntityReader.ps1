param([Parameter(Mandatory=$true)][string]$TransportPublicKeyBase64)
$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\Elastic'
$result = Join-Path $root 'ad-entity-bootstrap-result.json'
$sealed = Join-Path $root 'ad-entity-bind-password.enc'
$name = 'soc-entity-reader'
$description = 'SOC managed read-only AD Entity Analytics bind account'
try {
    Import-Module ActiveDirectory
    $domain = Get-ADDomain
    if ($domain.DNSRoot -ne 'sevenkingdoms.local') { throw 'Expected GOAD-Light root domain' }
    $existing = Get-ADUser -Filter {SamAccountName -eq $name} -Properties Description,MemberOf,PrimaryGroupID
    if ($existing) {
        if ($existing.Description -ne $description -or -not (Test-Path $sealed)) {
            throw 'Existing account has no managed credential receipt; do not rotate it'
        }
    } else {
        $bytes = New-Object byte[] 48
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($bytes)
        $password = 'Aa9!' + [Convert]::ToBase64String($bytes)
        $secure = New-Object Security.SecureString
        foreach ($character in $password.ToCharArray()) { $secure.AppendChar($character) }
        $secure.MakeReadOnly()
        $rsa = New-Object Security.Cryptography.RSACryptoServiceProvider
        $rsa.FromXmlString([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TransportPublicKeyBase64)))
        $ciphertext = [Convert]::ToBase64String($rsa.Encrypt([Text.Encoding]::UTF8.GetBytes($password), $true))
        [IO.File]::WriteAllText($sealed, $ciphertext)
        New-ADUser -Name $name -SamAccountName $name -UserPrincipalName "$name@$($domain.DNSRoot)" `
            -Path "CN=Users,$($domain.DistinguishedName)" -Description $description `
            -AccountPassword $secure -Enabled $true -ChangePasswordAtLogon $false -PasswordNeverExpires $true
        $secure.Dispose()
        $rng.Dispose()
        $rsa.Dispose()
        $password = $null
        $existing = Get-ADUser $name -Properties Description,MemberOf,PrimaryGroupID
    }
    if ($existing.MemberOf.Count -ne 0 -or $existing.PrimaryGroupID -ne 513) {
        throw 'Bind account must have only the default Domain Users membership'
    }
    $ca = Get-ChildItem Cert:\LocalMachine\My | Where-Object {
        $_.Thumbprint -eq 'CABA6DA5FCB8F099A929F627DB78AD100836B958' -and $_.Subject -eq $_.Issuer
    }
    if (-not $ca) { throw 'Reviewed GOAD enterprise CA not found' }
    $payload = @{success=$true; user_dn=$existing.DistinguishedName;
        user_upn="$name@$($domain.DNSRoot)"; password_ciphertext=[IO.File]::ReadAllText($sealed);
        ca_der_base64=[Convert]::ToBase64String($ca.RawData); additional_groups=@($existing.MemberOf)}
} catch {
    $payload = @{success=$false; error=$_.Exception.Message}
}
[IO.File]::WriteAllText($result, ($payload | ConvertTo-Json -Depth 3 -Compress))
