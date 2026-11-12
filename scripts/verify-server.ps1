param(
    [string]$SshTarget,
    [string]$Archive,
    [string]$StartDate = '2026-10-27',
    [string]$EndDate = '2026-10-28',
    [string]$ResourceTrialId,
    [string]$OverlapAfter,
    [switch]$WithAI,
    [switch]$Report
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
if ($Report) {
    $previous = Get-Content -LiteralPath '.validation/verification-latest.json' -Raw | ConvertFrom-Json
    if ($previous.report_id -notmatch '^verification-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid saved report ID' }
    $download = Join-Path (Get-Location) ('.validation/' + $previous.report_id + '.json')
    Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:/srv/example/finevent-backups/$($previous.report_id)/report.json" $download
    if ($LASTEXITCODE -ne 0) { throw 'Report download failed; no verification was restarted' }
    Write-Host "Report downloaded: $download"
    return
}
if ($ResourceTrialId -and $ResourceTrialId -notmatch '^trendradar-lazy-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid resource trial ID' }
if ($WithAI -and -not $ResourceTrialId) { throw 'WithAI requires the reviewed ResourceTrialId' }
if ($OverlapAfter -and (-not $WithAI -or $OverlapAfter -notmatch '^verification-\d{8}T\d{6}Z-[a-f0-9]{8}$')) { throw 'OverlapAfter requires WithAI and a valid preceding verification ID' }
foreach ($verificationDate in @($StartDate, $EndDate)) {
    if ($verificationDate -notmatch '^\d{4}-\d{2}-\d{2}$') { throw 'Dates must use YYYY-MM-DD' }
    [void][DateTime]::ParseExact($verificationDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
}
if (-not $Archive) {
    $Archive = (Get-ChildItem -LiteralPath 'release' -Filter 'finevent-*.tar.gz' -File | Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not $Archive -or -not (Test-Path -LiteralPath $Archive -PathType Leaf)) { throw 'Build the verification release first' }
& python scripts/check-release.py $Archive
if ($LASTEXITCODE -ne 0) { throw 'Release verification failed' }
$archiveName = Split-Path -Leaf $Archive
if ($archiveName -notmatch '^finevent-\d{8}T\d{6}Z\.tar\.gz$') { throw 'Unexpected release filename' }
$reportId = 'verification-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteStage = ConvertTo-FineventDeploymentText ('/srv/example/staging/' + $archiveName.Replace('.tar.gz',''))
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$reportId/report.json")
$aiArgument = if ($WithAI) { ' --with-ai' } else { '' }
$resourceArgument = if ($ResourceTrialId) { " --resource-trial-id '$ResourceTrialId'" } else { '' }
$overlapArgument = if ($OverlapAfter) { " --overlap-after '$OverlapAfter'" } else { '' }
New-Item -ItemType Directory -Path '.validation' -Force | Out-Null
$localReport = Join-Path (Get-Location) ".validation/$reportId.json"
@{ report_id=$reportId; remote_report=$remoteReport; local_report=$localReport; archive=$archiveName; resource_trial_id=$ResourceTrialId; overlap_after=$OverlapAfter } |
    ConvertTo-Json | Set-Content -LiteralPath '.validation/verification-latest.json' -Encoding utf8
if ($OverlapAfter) {
    Write-Host 'This upgrades the probe, waits up to 32 minutes for a new TrendRadar job, and sends ONE paid request. Prior rollback evidence is retained.'
} else {
    Write-Host 'This performs historical inspection, a probe release upgrade and a brief rollback/return rehearsal.'
}
Write-Host 'SSH/SCP passwords and the optional DeepSeek key are entered only in the terminal. No session transcript is recorded.'
Invoke-FineventScp -o StrictHostKeyChecking=yes $Archive "${SshTarget}:/tmp/$archiveName"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed; no server verification started' }
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 $SshTarget "mkdir -p '$remoteStage' && tar -xzf '/tmp/$archiveName' -C '$remoteStage' && python3 '$remoteStage/deploy/verification.py' --start '$StartDate' --end '$EndDate' --report-id '$reportId'$aiArgument$resourceArgument$overlapArgument"
$verificationExit = $LASTEXITCODE
# Download only the deliberately limited report, never the database, config, API key or authentication session.
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localReport
$reportExit = $LASTEXITCODE
Write-Host "Verification exit: $verificationExit; report download exit: $reportExit"
Write-Host "Report: $localReport"
if ($verificationExit -ne 0 -or $reportExit -ne 0) { throw 'Verification is incomplete. Keep the report and remote evidence for review; do not rerun blindly.' }
