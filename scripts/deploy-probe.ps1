param(
    [string]$SshTarget,
    [string]$Archive
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
if (-not $Archive) {
    $Archive = (Get-ChildItem -LiteralPath 'release' -Filter 'finevent-*.tar.gz' -File | Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not $Archive -or -not (Test-Path -LiteralPath $Archive -PathType Leaf)) { throw 'Build a release first' }
& python scripts/check-release.py $Archive
if ($LASTEXITCODE -ne 0) { throw 'Release verification failed; nothing uploaded' }
$archiveName = Split-Path -Leaf $Archive
if ($archiveName -notmatch '^finevent-\d{8}T\d{6}Z\.tar\.gz$') { throw 'Unexpected release filename' }
$remoteStage = ConvertTo-FineventDeploymentText ('/srv/example/staging/' + $archiveName.Replace('.tar.gz',''))
Write-Host 'Upload and SSH may each ask for the server password. Enter it only in this terminal.'
$uploadExit = $null
$deploymentExit = $null
try {
    Invoke-FineventScp -o StrictHostKeyChecking=yes $Archive "${SshTarget}:/tmp/$archiveName"
    $uploadExit = $LASTEXITCODE
    if ($uploadExit -ne 0) { throw 'Upload failed' }
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -p '$remoteStage' && tar -xzf '/tmp/$archiveName' -C '$remoteStage' && python3 '$remoteStage/deploy/install.py'"
    $deploymentExit = $LASTEXITCODE
    if ($deploymentExit -ne 0) { throw 'Deployment did not finish. Read the remote error; prior services are preserved or restored.' }
} finally {
    # Only fixed metadata and exit codes; never capture the interactive authentication session.
    New-Item -ItemType Directory -Path '.validation' -Force | Out-Null
    @{
        archive = $archiveName
        upload_exit = $uploadExit
        deployment_exit = $deploymentExit
        finished_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath '.validation/deployment-status.json' -Encoding utf8
}
Write-Host (ConvertTo-FineventDeploymentText 'Open https://www.example.com/finevent/ on phone and desktop to start notification tests.')
