param([string]$SshTarget, [switch]$Report)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
$previousArgument = ''
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
if ($Report) {
    $trial = Get-Content -LiteralPath '.validation/trendradar-lazy-latest.json' -Raw | ConvertFrom-Json
    if ($trial.id -notmatch '^trendradar-lazy-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid trial ID' }
    $trialId = $trial.id
} else {
    if (Test-Path -LiteralPath '.validation/trendradar-lazy-latest.json') {
        $previous = Get-Content -LiteralPath '.validation/trendradar-lazy-latest.json' -Raw | ConvertFrom-Json
        if ($previous.id -notmatch '^trendradar-lazy-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid preceding trial ID' }
        $previousReport = Join-Path (Get-Location) ".validation/$($previous.id)/report.json"
        if (-not (Test-Path -LiteralPath $previousReport)) { throw 'The preceding trial outcome is unknown; use -Report first.' }
        $previousStatus = Get-Content -LiteralPath $previousReport -Raw | ConvertFrom-Json
        if ($previousStatus.status -in @('queued', 'observing')) { throw 'A trial is already pending; use -Report instead of starting another.' }
        $previousArgument = " --previous-run '$($previous.id)'"
    }
    $workflow = Get-Content -LiteralPath '.validation/workflow-probe-latest.json' -Raw | ConvertFrom-Json
    if ($workflow.id -notmatch '^workflow-probe-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid workflow ID' }
    $trialId = 'trendradar-lazy-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
}
$remoteDirectory = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$trialId")
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$trialId")
$localDirectory = Join-Path (Get-Location) ".validation/$trialId"
$localReport = Join-Path $localDirectory 'report.json'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
if (-not $Report) {
    @{ id=$trialId; remote_report="$remoteDirectory/report.json"; local_report=$localReport; workflow_id=$workflow.id } |
        ConvertTo-Json | Set-Content -LiteralPath '.validation/trendradar-lazy-latest.json' -Encoding utf8
    Write-Host 'Trial one backed-up source change in the current TrendRadar container; keep current sources, HTML, Docker and memory settings.'
    Write-Host 'The server observes two normal collection cycles in the background (about 35-65 minutes). SSH may be closed after launch.'
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare upload directory; trial not launched' }
    $trialFiles = @('deploy/trendradar_lazy_trial.py', 'deploy/trendradar_import_probe.py', 'deploy/memory_recovery.py', 'deploy/memory_diagnostic.py', 'deploy/memory_high_trial.py')
    Invoke-FineventScp -o StrictHostKeyChecking=yes @trialFiles "${SshTarget}:$remoteStage/"
    if ($LASTEXITCODE -ne 0) { throw 'Trial upload failed' }
    # Authentication stays interactive; do not transcript, pipe, or redirect SSH.
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 $SshTarget "python3 -B '$remoteStage/trendradar_lazy_trial.py' --run-id '$trialId' --workflow-id '$($workflow.id)'$previousArgument --launch"
    if ($LASTEXITCODE -ne 0) { throw "Launch outcome needs review. Keep $remoteDirectory; do not start another trial blindly." }
    Start-Sleep -Seconds 3
}
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "python3 -B '$remoteDirectory/trendradar_lazy_trial.py' --run-id '$trialId' --status"
if ($LASTEXITCODE -ne 0) { throw "Status export failed; server evidence remains in $remoteDirectory" }
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteDirectory/download.json" $localReport
if ($LASTEXITCODE -ne 0) { throw "Report download failed; server evidence remains in $remoteDirectory" }
$result = Get-Content -LiteralPath $localReport -Raw | ConvertFrom-Json
Write-Host "Report downloaded: $localReport"
Write-Host "Status: $($result.status)"
if ($result.error_type) {
    Write-Host "Failure: $($result.stage) / $($result.error_type)"
    if ($result.error_details.sqlite_name) { Write-Host "SQLite: $($result.error_details.sqlite_name)" }
    Write-Host "Rollback status: $($result.rollback.status)"
}
if ($result.planned_end) { Write-Host "Planned end: $($result.planned_end)" }
if ($result.status -in @('queued', 'observing')) {
    Write-Host 'After the planned end, run: .\scripts\test-trendradar-lazy.ps1 -Report'
} elseif ($result.status -ne 'completed_for_review') {
    Write-Warning 'This trial is not accepted. Keep the report for review before retrying.'
}
if ($result.observer_status.warning) { Write-Warning $result.observer_status.warning }
