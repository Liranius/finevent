param(
    [string]$SshTarget,
    [string]$VerificationId = 'verification-20261028T115003Z-1a6f51d3'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
if ($VerificationId -notmatch '^verification-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid verification ID' }
$diagnosticId = 'memory-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteScript = "/tmp/finevent-$diagnosticId.py"
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$VerificationId/$diagnosticId.json")
New-Item -ItemType Directory -Path '.validation' -Force | Out-Null
$localReport = Join-Path (Get-Location) ".validation/$diagnosticId.json"
@{ verification_id=$VerificationId; remote_report=$remoteReport; local_report=$localReport } |
    ConvertTo-Json | Set-Content -LiteralPath '.validation/memory-latest.json' -Encoding utf8
Write-Host 'Read-only diagnostics: existing snapshot, memory counters, process names and OOM evidence. No deployment or model call.'
Invoke-FineventScp -o StrictHostKeyChecking=yes 'deploy/memory_diagnostic.py' "${SshTarget}:$remoteScript"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed; no diagnostic started' }
# Authentication stays in the terminal; never record the interactive session.
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "python3 '$remoteScript' --verification-id '$VerificationId' --report-name '$diagnosticId.json'"
if ($LASTEXITCODE -ne 0) { throw 'Read-only diagnostic did not finish; retain the displayed error' }
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localReport
if ($LASTEXITCODE -ne 0) { throw "Report download failed; evidence remains at $remoteReport" }
Write-Host "Report: $localReport"
