param([string]$SshTarget, [string]$Archive, [string]$AfterReview, [string]$RunId, [switch]$Report, [switch]$Rollback)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$target = Initialize-FineventDeployment -SshTarget $SshTarget
if ($Report) {
    if ($Archive -or $AfterReview -or $Rollback) { throw 'Report only takes an optional operation ID' }
    if (-not $RunId) { $RunId = 'latest-web' }
    $arguments = @('status', '--id', $RunId)
} else {
    if (-not $AfterReview) { throw 'Specify the latest web operation ID, or initial for the first release' }
    if ($Rollback) {
        if ($Archive) { throw 'Rollback uses the previous installed web release' }
        $arguments = @('web-rollback')
    } else {
        if (-not $Archive) { throw 'Specify the web archive to publish' }
        $arguments = @('web-deploy', '--archive', $Archive)
    }
    $arguments += @('--after-review', $AfterReview)
    if ($RunId) { $arguments += @('--id', $RunId) }
}
& python (Join-Path $PSScriptRoot 'release-client.py') @arguments --target $target
if ($LASTEXITCODE -ne 0) { throw 'Web operation needs review; query its report before retrying' }
