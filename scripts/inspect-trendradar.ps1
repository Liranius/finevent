param([string]$SshTarget, [switch]$Persistence)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$auditId = 'trendradar-audit-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$auditId")
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$auditId/report.json")
$localDirectory = Join-Path (Get-Location) ".validation/$auditId"
$localReport = Join-Path $localDirectory 'report.json'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
$trialArgument = ''
$latestTrial = $null
if ($Persistence) {
    $verification = Get-Content -LiteralPath '.validation/verification-latest.json' -Raw | ConvertFrom-Json
    if ($verification.report_id -notmatch '^verification-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid latest verification ID' }
    $trialArgument = " --persistence-verification '$($verification.report_id)'"
} elseif (Test-Path -LiteralPath '.validation/memory-high-latest.json') {
    $latestTrial = Get-Content -LiteralPath '.validation/memory-high-latest.json' -Raw | ConvertFrom-Json
    if ($latestTrial.id -notmatch '^memory-high-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid latest trial ID' }
    $trialArgument = " --latest-trial '$($latestTrial.id)'"
}
@{ id=$auditId; remote_report=$remoteReport; local_report=$localReport } |
    ConvertTo-Json | Set-Content -LiteralPath '.validation/trendradar-audit-latest.json' -Encoding utf8
if ($Persistence) {
    Write-Host 'Read-only persistence preparation: Compose deployment, cgroup settings and swappiness definitions. No AI call or service change.'
} else {
    Write-Host 'Read-only inventory: selected feature flags, static imports, process identity and web routing. No collection, AI call or configuration change.'
}
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the audit upload directory' }
Invoke-FineventScp -o StrictHostKeyChecking=yes 'deploy/trendradar_audit.py' 'deploy/memory_diagnostic.py' "${SshTarget}:$remoteStage/"
if ($LASTEXITCODE -ne 0) { throw 'Audit upload failed' }
# Password authentication remains interactive and is never recorded.
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 $SshTarget "python3 -B '$remoteStage/trendradar_audit.py' --run-id '$auditId'$trialArgument"
$auditExit = $LASTEXITCODE
Write-Host 'Enter the server password again when prompted to download the audit report.'
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localReport
$reportExit = $LASTEXITCODE
if ($latestTrial) {
    $trialDirectory = Join-Path (Get-Location) ".validation/$($latestTrial.id)"
    New-Item -ItemType Directory -Path $trialDirectory -Force | Out-Null
    # Preserve any already-downloaded evidence; this is a separately named snapshot.
    $trialReport = Join-Path $trialDirectory 'report-audit-download.json'
    Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:/srv/example/finevent-backups/$($latestTrial.id)/report.json" $trialReport
    Write-Host "Latest trial report download exit: $LASTEXITCODE; path: $trialReport"
}
Write-Host "Audit exit: $auditExit; report download exit: $reportExit"
if ($auditExit -ne 0 -or $reportExit -ne 0) { throw "Audit incomplete; preserve the report at $remoteReport for review." }
Write-Host "Report downloaded: $localReport"
