param([string]$SshTarget)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$runId = 'retire-handy-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$runId")
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$runId/report.json")
$localDirectory = Join-Path (Get-Location) ".validation/$runId"
$localReport = Join-Path $localDirectory 'report.json'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
@{ id=$runId; remote_report=$remoteReport; local_report=$localReport } |
    ConvertTo-Json | Set-Content -LiteralPath '.validation/retire-handy-latest.json' -Encoding utf8
Write-Host 'Stop and disable only handy-stock-api.service after checking its identity. Preserve application files and data.'
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the upload directory; no service changed' }
Invoke-FineventScp -o StrictHostKeyChecking=yes 'deploy/retire_handy_stock.py' 'deploy/memory_recovery.py' 'deploy/memory_diagnostic.py' "${SshTarget}:$remoteStage/"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed; no service changed' }
# Authentication stays interactive and is never recorded.
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 $SshTarget "python3 -B '$remoteStage/retire_handy_stock.py' --run-id '$runId'"
$operationExit = $LASTEXITCODE
Write-Host 'Enter the server password again when prompted to download the result.'
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localReport
$reportExit = $LASTEXITCODE
Write-Host "Operation exit: $operationExit; report download exit: $reportExit"
if ($operationExit -ne 0 -or $reportExit -ne 0) { throw "Operation needs review; retain $remoteReport. Do not delete application files." }
Write-Host "Report downloaded: $localReport"
