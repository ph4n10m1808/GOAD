$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
$base = 'HKLM:\SYSTEM\CurrentControlSet\Services\CertSvc\Configuration'
$active = (Get-ItemProperty -Path $base -Name Active).Active
if ([string]::IsNullOrWhiteSpace($active)) { throw 'No configured CA to audit' }
$caPath = Join-Path $base $active
$policyPath = Join-Path $caPath 'PolicyModules\CertificateAuthority_MicrosoftDefault.Policy'
$oldAudit = [int](Get-ItemProperty $caPath -Name AuditFilter -ErrorAction SilentlyContinue).AuditFilter
$oldFlags = [int](Get-ItemProperty $policyPath -Name EditFlags).EditFlags
# Audit bits are additive: do not clear ESC6 or any other policy flag.
if (($oldAudit -band 127) -ne 127) {
    & certutil.exe -setreg 'CA\AuditFilter' ($oldAudit -bor 127) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot configure CA audit filter' }
    $Ansible.Changed = $true
}
# EDITF_AUDITCERTTEMPLATELOAD controls template-load/change audit events.
if (($oldFlags -band 0x200000) -eq 0) {
    & certutil.exe -setreg 'policy\EditFlags' '+EDITF_AUDITCERTTEMPLATELOAD' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot configure template audit flag' }
    $Ansible.Changed = $true
}
if ($Ansible.Changed) { Restart-Service CertSvc -ErrorAction Stop }
$service = Get-Service CertSvc
$service.WaitForStatus('Running', [TimeSpan]::FromSeconds(120))
$audit = [int](Get-ItemProperty $caPath -Name AuditFilter).AuditFilter
$flags = [int](Get-ItemProperty $policyPath -Name EditFlags).EditFlags
if ($audit -ne ($oldAudit -bor 127) -or $flags -ne ($oldFlags -bor 0x200000)) {
    throw 'CA audit readback differs or unrelated flags changed'
}
[pscustomobject]@{ca=$active;audit_filter=$audit;template_audit=$true;existing_policy_flags_preserved=$true}
