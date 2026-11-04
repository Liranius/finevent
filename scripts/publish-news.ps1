param([string]$SshTarget, [string]$Archive, [switch]$Report, [string]$AfterReview)
$ErrorActionPreference = 'Stop'
# Share one operation pointer and remote lock with calendar releases; never run two observers.
& (Join-Path $PSScriptRoot 'publish-calendar.ps1') @PSBoundParameters -News
