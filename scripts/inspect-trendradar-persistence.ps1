param([string]$SshTarget)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$previous = Get-Content -LiteralPath '.validation/trendradar-persist-latest.json' -Raw | ConvertFrom-Json
if ($previous.id -notmatch '^trendradar-persist-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid preceding persistence ID' }
$runId = 'trendradar-persist-review-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$runId")
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$runId/report.json")
$localDirectory = Join-Path (Get-Location) ".validation/$runId"
$localReport = Join-Path $localDirectory 'report.json'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
@{ id=$runId; previous_run=$previous.id; remote_report=$remoteReport; local_report=$localReport } |
    ConvertTo-Json | Set-Content -LiteralPath '.validation/trendradar-persist-review-latest.json' -Encoding utf8
Write-Host 'Read-only diagnosis: verify the original container/configuration and compare Compose network structure. No deployment retry or restart.'
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare diagnostic upload directory' }
$files = @('deploy/trendradar_persist_review.py', 'deploy/trendradar_persist.py', 'deploy/trendradar_audit.py',
    'deploy/verification_resources.py', 'deploy/trendradar_lazy_trial.py', 'deploy/trendradar_import_probe.py',
    'deploy/memory_recovery.py', 'deploy/memory_diagnostic.py', 'deploy/memory_high_trial.py')
Invoke-FineventScp -o StrictHostKeyChecking=yes @files "${SshTarget}:$remoteStage/"
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic upload failed' }
# Authentication remains interactive and is never recorded.
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 $SshTarget "python3 -B '$remoteStage/trendradar_persist_review.py' --run-id '$runId' --previous-run '$($previous.id)'"
$diagnosticExit = $LASTEXITCODE
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localReport
$downloadExit = $LASTEXITCODE
Write-Host "Diagnosis exit: $diagnosticExit; report download exit: $downloadExit"
Write-Host "Report: $localReport"
if ($diagnosticExit -ne 0 -or $downloadExit -ne 0) { throw 'Diagnosis incomplete; preserve the report for review.' }
