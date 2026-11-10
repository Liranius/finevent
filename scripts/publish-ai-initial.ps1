param([string]$SshTarget, [string]$Archive, [switch]$Report, [string]$AfterReview, [string]$ReplaceObservation, [switch]$News, [switch]$AI)
# First-AI initialization requires an inherited TTY and a hidden key prompt.
# Ordinary releases and all report downloads use publish-calendar.ps1.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
if (-not $AI -or $Report -or $News -or $ReplaceObservation) { throw 'This entrypoint only initializes AI interactively; use publish-calendar.ps1 for releases/reports' }
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
if ($AfterReview -and $ReplaceObservation) { throw 'Choose either -AfterReview or -ReplaceObservation' }
if ($News -and $ReplaceObservation) { throw 'News verification requires the previous observation to finish and be reviewed' }
if ($AI -and ($News -or $ReplaceObservation -or (-not $Report -and -not $AfterReview))) { throw 'AI verification requires a reviewed predecessor and its own profile' }
$predecessor = if ($ReplaceObservation) { $ReplaceObservation } else { $AfterReview }
$latest = '.validation/calendar-release-latest.json'
if ($Report) {
    if ($Archive -or $predecessor) { throw '-Report cannot be combined with release or predecessor options' }
    $operation = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
    $runId = $operation.id
    $archiveName = $operation.archive
} else {
    if (Test-Path -LiteralPath $latest) {
        $previous = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
        if (-not $predecessor -or $predecessor -ne $previous.id -or $predecessor -notmatch '^calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}$') {
            throw 'Use -Report first. Specify the exact previous operation ID with -AfterReview or -ReplaceObservation.'
        }
        $previousDirectory = Join-Path (Get-Location) ".validation/$predecessor"
        $previousReport = Get-Content -LiteralPath (Join-Path $previousDirectory 'report.json') -Raw | ConvertFrom-Json
        $completed = $previousReport.stage -eq 'completed' -and $previousReport.status -in @('completed_for_review','completed_with_source_limits_for_review','needs_review')
        $observing = $previousReport.stage -eq 'scheduled_sync_and_resources' -and $previousReport.status -eq 'observing' -and
            $previousReport.restart.status -eq 'passed' -and $previousReport.backup_verification.status -eq 'passed'
        if ($previousReport.id -ne $predecessor -or $previousReport.rollback -or $previousReport.failure -or
            (-not $completed -and -not ($ReplaceObservation -and $observing))) {
            throw 'The previous report cannot be replaced in this state. Download and review it before retrying.'
        }
        Copy-Item -LiteralPath $latest -Destination (Join-Path $previousDirectory 'operation.json') -Force
    } elseif ($predecessor) { throw 'Previous operation pointer is missing; do not invent a predecessor.' }
    if (-not $Archive) {
        $Archive = (Get-ChildItem -LiteralPath 'release' -Filter 'finevent-*.tar.gz' -File | Sort-Object Name -Descending | Select-Object -First 1).FullName
    }
    if (-not $Archive -or -not (Test-Path -LiteralPath $Archive -PathType Leaf)) { throw 'Build the calendar release first' }
    & python scripts/check-release.py $Archive
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed; nothing uploaded' }
    & python -c "import sys,tarfile; t=tarfile.open(sys.argv[1]); assert any(m.name.lstrip('./') == 'deploy/calendar_release.py' for m in t.getmembers()), 'Calendar release runner missing'" $Archive
    if ($LASTEXITCODE -ne 0) { throw 'This archive does not contain the calendar deployment workflow' }
    if ($News) {
        & python -c "import sys,tarfile; t=tarfile.open(sys.argv[1]); m=next(m for m in t.getmembers() if m.name.lstrip('./') == 'deploy/calendar_release.py'); assert b'--news' in t.extractfile(m).read(); assert any(m.name.lstrip('./') == 'deploy/news_release.py' for m in t.getmembers()), 'News verification runner missing'" $Archive
        if ($LASTEXITCODE -ne 0) { throw 'Build the news release before using news verification' }
    }
    if ($AI) {
        & python -c "import sys,tarfile; t=tarfile.open(sys.argv[1]); m=next(m for m in t.getmembers() if m.name.lstrip('./') == 'deploy/calendar_release.py'); assert b'--ai' in t.extractfile(m).read(); assert any(m.name.lstrip('./') == 'deploy/ai_release.py' for m in t.getmembers()), 'AI verification runner missing'" $Archive
        if ($LASTEXITCODE -ne 0) { throw 'Build the AI release before using AI verification' }
    }
    if ($ReplaceObservation) {
        & python -c "import sys,tarfile; t=tarfile.open(sys.argv[1]); m=next(m for m in t.getmembers() if m.name.lstrip('./') == 'deploy/calendar_release.py'); assert b'--replace-observation' in t.extractfile(m).read(), 'Observer replacement is not supported by this archive'" $Archive
        if ($LASTEXITCODE -ne 0) { throw 'Build the updated release before replacing an observation' }
    }
    $archiveName = Split-Path -Leaf $Archive
    $runId = 'calendar-release-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
}
if ($runId -notmatch '^calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}$' -or $archiveName -notmatch '^finevent-\d{8}T\d{6}Z\.tar\.gz$') { throw 'Invalid operation or archive identity' }
$remoteStage = ConvertTo-FineventDeploymentText ('/srv/example/staging/' + $archiveName.Replace('.tar.gz',''))
$remoteDirectory = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$runId")
$localDirectory = Join-Path (Get-Location) ".validation/$runId"
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
if (-not $Report) {
    $reviewArgument = ''
    if ($AfterReview) { $reviewArgument = " --after-review '$AfterReview'" }
    if ($ReplaceObservation) { $reviewArgument = " --replace-observation '$ReplaceObservation'" }
    if ($News) { $reviewArgument += ' --news' }
    if ($AI) { $reviewArgument += ' --ai' }
    @{ id=$runId; archive=$archiveName; archive_sha256=(Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant(); remote_report="$remoteDirectory/report.json"; verification_profile=$(if ($AI) { 'ai' } elseif ($News) { 'news' } else { 'calendar' }) } |
        ConvertTo-Json | Set-Content -LiteralPath $latest -Encoding utf8
    Write-Host 'Upload, release and verification use interactive SSH/SCP authentication. Enter passwords only in this terminal.'
    Write-Host 'The server checks the accepted persistent TrendRadar configuration, backs up finevent, installs the release and restarts finevent once.'
    if ($AI) {
        Write-Host 'Enter the DeepSeek key only at the hidden server terminal prompt. Keep this SSH connection until background launch is confirmed.'
        Write-Host 'One real news analysis waits for a normal TrendRadar collection (up to 32 minutes), then checks overlap, billing and restart. Automatic AI stays off; no paid retry.'
        Write-Host 'Do not enable AI or request analyses in the UI until this verification is reviewed. Report downloads never trigger a model call.'
    } elseif ($News) {
        Write-Host 'News migration and indexing are checked first (up to 20 minutes), then a 65-minute observer covers real APIs, collection and resources. No AI call or TrendRadar change.'
    } else {
        Write-Host 'A background observer then covers the next six-hour calendar synchronization (about 6 hours 15 minutes). No AI call and no TrendRadar change.'
    }
    if ($ReplaceObservation) { Write-Host "The old observer $ReplaceObservation will be stopped after saving its evidence; unfinished observation will not be accepted." }
    Invoke-FineventScp -o StrictHostKeyChecking=yes $Archive "${SshTarget}:/tmp/$archiveName"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed before launch; preserve the operation record for review' }
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 $SshTarget "mkdir -m 700 '$remoteStage' && tar -xzf '/tmp/$archiveName' -C '$remoteStage' && python3 -B '$remoteStage/deploy/calendar_release.py' --run-id '$runId' --launch$reviewArgument && python3 -B '$remoteStage/deploy/calendar_release.py' --run-id '$runId' --status --wait-ready 180"
    if ($LASTEXITCODE -ne 0) { throw "Launch outcome needs review. Use -Report; do not reapply. Evidence: $remoteDirectory" }
} else {
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "python3 -B '$remoteStage/deploy/calendar_release.py' --run-id '$runId' --status"
    if ($LASTEXITCODE -ne 0) { throw "Status export failed. The background operation may still be running. Evidence: $remoteDirectory" }
}
$localReport = Join-Path $localDirectory 'report.json'
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteDirectory/download.json" $localReport
if ($LASTEXITCODE -ne 0) { throw "Report download failed; the background operation is unaffected. Evidence: $remoteDirectory" }
$result = Get-Content -LiteralPath $localReport -Raw | ConvertFrom-Json
Write-Host "Report: $localReport"
Write-Host "Status: $($result.status) / $($result.stage)"
if ($result.planned_end) { Write-Host "Planned end: $($result.planned_end)" }
if ($result.failure) { Write-Warning $result.failure }
if ($result.observer_status.warning) { Write-Warning $result.observer_status.warning }
if ($operation.verification_profile -eq 'ai' -or $AI) {
    Write-Host 'To download progress or the final report without redeploying: .\scripts\publish-ai.ps1 -Report'
} else {
    Write-Host 'To download progress or the final report without redeploying: .\scripts\publish-calendar.ps1 -Report'
}
if ($result.status -in @('incomplete','needs_review','rolled_back')) { Write-Warning 'This operation has not passed acceptance. Keep its evidence for review.' }
