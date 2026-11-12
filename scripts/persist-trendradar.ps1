param([string]$SshTarget, [switch]$Report, [string]$AfterReview)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$latest = '.validation/trendradar-persist-latest.json'
$reviewArgument = ''
if ($Report -and $AfterReview) { throw '-Report cannot be combined with -AfterReview' }
if ($Report) {
    $operation = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
    if ($operation.id -notmatch '^trendradar-persist-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid persistence ID' }
    $runId = $operation.id
} else {
    if ($AfterReview) {
        if ($AfterReview -notmatch '^trendradar-persist-review-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid review ID' }
        $review = Get-Content -LiteralPath ".validation/$AfterReview/report.json" -Raw | ConvertFrom-Json
        $previous = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
        if ($previous.id -notmatch '^trendradar-persist-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid preceding persistence ID' }
        $previousReport = Get-Content -LiteralPath ".validation/$($previous.id)/report.json" -Raw | ConvertFrom-Json
        if ($review.id -ne $AfterReview -or $review.status -ne 'collected_for_review' -or $review.stage -ne 'completed' -or
            $review.previous_run -ne $previous.id -or $previousReport.status -ne 'incomplete' -or
            $previousReport.rollback.status -ne 'restored') {
            throw 'The review must match the latest restored failure. A pending operation cannot be retried.'
        }
        $reviewArgument = " --review-id '$AfterReview'"
    } elseif (Test-Path -LiteralPath $latest) {
        throw 'A persistence operation was already prepared. Use -Report and review its outcome before another operation.'
    }
    $audit = Get-Content -LiteralPath '.validation/trendradar-audit-latest.json' -Raw | ConvertFrom-Json
    if ($audit.id -notmatch '^trendradar-audit-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid audit ID' }
    $auditReport = Get-Content -LiteralPath ".validation/$($audit.id)/report.json" -Raw | ConvertFrom-Json
    if ($auditReport.status -ne 'collected_for_review' -or $auditReport.stage -ne 'deployment_inventory') {
        throw 'A completed persistence inventory is required.'
    }
    $runId = 'trendradar-persist-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
}
$remoteDirectory = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$runId")
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$runId")
$localDirectory = Join-Path (Get-Location) ".validation/$runId"
$localReport = Join-Path $localDirectory 'report.json'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
if (-not $Report) {
    @{ id=$runId; remote_report="$remoteDirectory/report.json"; local_report=$localReport; audit_id=$audit.id; review_id=$AfterReview } |
        ConvertTo-Json | Set-Content -LiteralPath $latest -Encoding utf8
    Write-Host 'Persist the tested patch, RAM/swap/reclaim limits and host swappiness, retaining the original container for rollback.'
    Write-Host 'TrendRadar will be recreated and restarted briefly. Existing HTML/data remain available; no paid model call.'
    Write-Host 'The server then observes two normal cycles in the background (about 35-65 minutes).'
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare upload directory; persistence not launched' }
    $files = @('deploy/trendradar_persist.py', 'deploy/trendradar_audit.py', 'deploy/verification_resources.py',
        'deploy/trendradar_lazy_trial.py', 'deploy/trendradar_import_probe.py', 'deploy/memory_recovery.py',
        'deploy/memory_diagnostic.py', 'deploy/memory_high_trial.py')
    Invoke-FineventScp -o StrictHostKeyChecking=yes @files "${SshTarget}:$remoteStage/"
    if ($LASTEXITCODE -ne 0) { throw 'Persistence upload failed' }
    # Keep password authentication interactive; never transcript or redirect it.
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 $SshTarget "python3 -B '$remoteStage/trendradar_persist.py' --run-id '$runId' --audit-id '$($audit.id)'$reviewArgument --launch"
    if ($LASTEXITCODE -ne 0) { throw "Launch outcome needs review. Keep $remoteDirectory and use -Report; do not reapply." }
    Start-Sleep -Seconds 3
}
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "python3 -B '$remoteDirectory/trendradar_persist.py' --run-id '$runId' --status"
if ($LASTEXITCODE -ne 0) { throw "Status export failed; server evidence remains in $remoteDirectory" }
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteDirectory/download.json" $localReport
if ($LASTEXITCODE -ne 0) { throw "Report download failed; server evidence remains in $remoteDirectory" }
$result = Get-Content -LiteralPath $localReport -Raw | ConvertFrom-Json
Write-Host "Report downloaded: $localReport"
Write-Host "Status: $($result.status) / $($result.stage)"
if ($result.error_type) { Write-Host "Failure: $($result.error_type); rollback: $($result.rollback.status)" }
if ($result.configuration_error) { Write-Host "Configuration check: $($result.configuration_error.code); fields: $($result.configuration_error.fields -join ', ')" }
if ($result.planned_end) { Write-Host "Planned end: $($result.planned_end)" }
if ($result.status -in @('queued', 'applying', 'observing')) {
    Write-Host 'After the planned end, run: .\scripts\persist-trendradar.ps1 -Report'
} elseif ($result.status -ne 'completed_for_review') {
    Write-Warning 'Persistence is not accepted. Keep the report for review; do not retry blindly.'
}
if ($result.observer_status.warning) { Write-Warning $result.observer_status.warning }
