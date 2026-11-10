param([string]$SshTarget, [string]$Archive, [switch]$Report, [string]$AfterReview, [string]$RunId,
    [string]$BaselineId, [string]$BaselineSha256, [string]$RecoverPreflight, [string]$FailedReportSha256)
$ErrorActionPreference = 'Stop'
# Share the server operation registry and observer lock with calendar releases.
& (Join-Path $PSScriptRoot 'publish-calendar.ps1') @PSBoundParameters -News
