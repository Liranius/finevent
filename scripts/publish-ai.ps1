param([string]$SshTarget, [string]$Archive, [switch]$Report, [string]$AfterReview, [string]$RunId)
$ErrorActionPreference = 'Stop'
# First initialization keeps its hidden TTY prompt; reports use the shared controller.
& (Join-Path $PSScriptRoot 'publish-calendar.ps1') @PSBoundParameters -AI
