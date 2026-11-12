param([string]$SshTarget, [string]$Archive, [switch]$Report, [string]$AfterReview, [string]$ReplaceObservation, [switch]$News, [switch]$AI, [string]$RunId,
    [string]$BaselineId, [string]$BaselineSha256, [string]$RecoverPreflight, [string]$FailedReportSha256)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($AI -and -not $Report) {
    if ($RunId -or $BaselineId -or $BaselineSha256 -or $RecoverPreflight -or $FailedReportSha256) { throw 'Interactive AI initialization does not accept recovery or baseline options' }
    # Refresh full predecessor evidence before the separate TTY flow.
    & python scripts/release-client.py status --target $SshTarget --private-report
    if ($LASTEXITCODE -ne 0) { throw 'Review the latest server operation before initializing AI' }
    & (Join-Path $PSScriptRoot 'publish-ai-initial.ps1') @PSBoundParameters
    return
}
$arguments = @($(if ($Report) { 'status' } else { 'deploy' }), '--target', $SshTarget, '--private-report')
if ($RunId) { $arguments += @('--id', $RunId) }
if ($Report) {
    if ($Archive -or $AfterReview -or $ReplaceObservation -or $BaselineId -or $BaselineSha256 -or $RecoverPreflight -or $FailedReportSha256) { throw '-Report cannot be combined with deployment options' }
} else {
    if (-not $Archive) { $Archive = (Get-ChildItem -LiteralPath 'release' -Filter 'finevent-*.tar.gz' -File | Sort-Object Name -Descending | Select-Object -First 1).FullName }
    if (-not $Archive) { throw 'Build a release first' }
    $arguments += @('--archive', $Archive, '--profile', $(if ($News) { 'news' } else { 'calendar' }))
    if ($AfterReview) { $arguments += @('--after-review', $AfterReview) }
    if ($ReplaceObservation) { $arguments += @('--replace-observation', $ReplaceObservation) }
    foreach ($entry in @(@('baseline-id', $BaselineId), @('baseline-sha256', $BaselineSha256), @('recover-preflight', $RecoverPreflight), @('failed-report-sha256', $FailedReportSha256))) {
        if ($entry[1]) { $arguments += @(('--' + $entry[0]), $entry[1]) }
    }
}
& python scripts/release-client.py @arguments
if ($LASTEXITCODE -ne 0) { throw 'Release needs review. Query the same -RunId with -Report; do not create a new operation to retry.' }
Write-Host 'Server operation recorded. A successful query/launch does not mean observation or user acceptance passed.'
