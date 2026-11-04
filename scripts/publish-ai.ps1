param([string]$SshTarget, [string]$Archive, [switch]$Report, [string]$AfterReview)
$ErrorActionPreference = 'Stop'
# A shared operation pointer/lock prevents overlapping calendar, news and AI releases.
& (Join-Path $PSScriptRoot 'publish-calendar.ps1') @PSBoundParameters -AI
