param([string]$SshTarget)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$recoveryId = 'memory-recovery-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$recoveryId")
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$recoveryId/report.json")
$localDirectory = Join-Path (Get-Location) ".validation/$recoveryId"
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
@{ id=$recoveryId; remote_report=$remoteReport; local_report=(Join-Path $localDirectory 'report.json') } |
    ConvertTo-Json | Set-Content -LiteralPath '.validation/memory-recovery-latest.json' -Encoding utf8
Write-Host 'Authorized trial: 1 GiB host swap, TrendRadar 192 MiB RAM + up to 256 MiB swap, followed by two normal collection cycles.'
Write-Host 'Allow about 35-65 minutes and keep this terminal open. No model credentials or paid calls are involved.'
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare upload directory; no service settings changed' }
Invoke-FineventScp -o StrictHostKeyChecking=yes 'deploy/memory_recovery.py' 'deploy/memory_diagnostic.py' "${SshTarget}:$remoteStage/"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed; no recovery started' }
# Keep authentication in the interactive terminal. Never transcript or redirect this session.
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 $SshTarget "python3 '$remoteStage/memory_recovery.py' --run-id '$recoveryId'"
$recoveryExit = $LASTEXITCODE
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localDirectory
$reportExit = $LASTEXITCODE
Write-Host "Recovery exit: $recoveryExit; report download exit: $reportExit"
Write-Host "Report: $(Join-Path $localDirectory 'report.json')"
if ($recoveryExit -ne 0 -or $reportExit -ne 0) { throw 'Recovery needs review. Keep the report; do not rerun apply or disable swap blindly.' }
