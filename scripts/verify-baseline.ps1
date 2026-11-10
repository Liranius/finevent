param([string]$SshTarget, [string]$Archive, [string]$RunId, [switch]$Report)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$arguments = @($(if ($Report) { 'status' } else { 'baseline' }), '--target', $SshTarget, '--private-report')
if ($RunId) { $arguments += @('--id', $RunId) }
if ($Report) {
    if (-not $RunId -or $Archive) { throw 'Baseline report requires its exact RunId and no archive' }
} else {
    if (-not $Archive) { throw 'Specify a verified rendered deployment archive' }
    $arguments += @('--archive', $Archive)
}
& python scripts/release-client.py @arguments
if ($LASTEXITCODE -ne 0) { throw 'Read-only baseline needs review; preserve its operation ID and evidence' }
