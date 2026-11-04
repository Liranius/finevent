param([string]$SshTarget, [switch]$Report)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$latest = '.validation/calendar-observation-review-latest.json'
if ($Report) {
    $operation = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
    $runId = $operation.id
    $releaseId = $operation.release_id
} else {
    $release = Get-Content -LiteralPath '.validation/calendar-release-latest.json' -Raw | ConvertFrom-Json
    $releaseId = $release.id
    if ($releaseId -notmatch '^calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid calendar release identity' }
    $observation = Get-Content -LiteralPath ".validation/$releaseId/report.json" -Raw | ConvertFrom-Json
    if ($observation.id -ne $releaseId -or $observation.stage -ne 'completed') { throw 'Download the completed observation first' }
    $runId = 'calendar-observation-review-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
}
if ($runId -notmatch '^calendar-observation-review-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid review identity' }
if ($releaseId -notmatch '^calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid calendar release identity' }
$localDirectory = Join-Path (Get-Location) ".validation/$runId"
$remoteDirectory = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$runId")
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
if (-not $Report) {
    @{ id=$runId; release_id=$releaseId; remote_report="$remoteDirectory/report.json" } |
        ConvertTo-Json | Set-Content -LiteralPath $latest -Encoding utf8
    Write-Host 'Read-only review of existing crawl records and recovery after source failures. No restart, collection, deployment or AI call.'
    Invoke-FineventScp -o StrictHostKeyChecking=yes 'deploy/calendar_observation_review.py' "${SshTarget}:/tmp/$runId.py"
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic upload failed' }
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 $SshTarget "python3 -B '/tmp/$runId.py' --run-id '$runId' --release-id '$releaseId'"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Review may be incomplete. Downloading its evidence; do not rerun blindly.'
    }
}
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteDirectory/report.json" (Join-Path $localDirectory 'report.json')
if ($LASTEXITCODE -ne 0) { throw 'Report download failed. Use .\scripts\inspect-calendar-observation.ps1 -Report to retrieve the same evidence.' }
$result = Get-Content -LiteralPath (Join-Path $localDirectory 'report.json') -Raw | ConvertFrom-Json
Write-Host "Report downloaded: $localDirectory/report.json"
Write-Host "Status: $($result.status); source failures: $(@($result.observation_source_failures).Count); original counts match: $($result.original_counts_match)"
if ($result.status -ne 'completed_for_review') { Write-Warning 'Review incomplete; preserve the downloaded report.' }
