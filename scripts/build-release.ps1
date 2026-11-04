$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$null = Initialize-FineventDeployment
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& fnm exec --using 24.21.0 pnpm build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
$releaseStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$releaseRoot = Join-Path (Get-Location) "release/$releaseStamp"
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
$oldGoOS = $env:GOOS
$oldGoArch = $env:GOARCH
$oldCgo = $env:CGO_ENABLED
try {
    $env:GOOS = 'linux'
    $env:GOARCH = 'amd64'
    $env:CGO_ENABLED = '0'
    & go -C apps/server build -trimpath '-ldflags=-s -w' -o (Join-Path $releaseRoot 'finevent') ./cmd/finevent
    if ($LASTEXITCODE -ne 0) { throw 'Linux binary build failed' }
} finally {
    $env:GOOS = $oldGoOS
    $env:GOARCH = $oldGoArch
    $env:CGO_ENABLED = $oldCgo
}
Copy-Item -LiteralPath 'apps/web/dist' -Destination $releaseRoot -Recurse
$releaseDeploy = Join-Path $releaseRoot 'deploy'
New-Item -ItemType Directory -Path $releaseDeploy | Out-Null
Copy-Item -LiteralPath 'apps/server/internal/calendar/snapshots/nbs-2026.json' -Destination (Join-Path $releaseDeploy 'calendar-snapshot.json')
foreach ($deployFile in @('install.py','backup.py','calendar_release.py','news_release.py','news_push_release.py','source_health_release.py','calendar_links_release.py','ai_release.py','calendar_review.py','calendar-reviews.json','verification.py','verification_resources.py','memory_recovery.py','memory_diagnostic.py','finevent.service','finevent-backup.service','finevent-backup.timer','finevent.nginx.conf')) {
    Export-FineventDeploymentFile (Join-Path 'deploy' $deployFile) (Join-Path $releaseDeploy $deployFile)
}
$releaseFiles = Get-ChildItem -LiteralPath $releaseRoot -File -Recurse
$manifest = @{}
foreach ($releaseFile in $releaseFiles) {
    $relative = [IO.Path]::GetRelativePath($releaseRoot, $releaseFile.FullName).Replace([IO.Path]::DirectorySeparatorChar, [char]'/')
    $manifest[$relative] = (Get-FileHash -LiteralPath $releaseFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot 'sha256.json') -Encoding utf8
$releaseArchive = Join-Path (Get-Location) "release/finevent-$releaseStamp.tar.gz"
& tar -czf $releaseArchive -C $releaseRoot .
if ($LASTEXITCODE -ne 0) { throw 'Archive creation failed' }
& python scripts/check-release.py $releaseArchive
if ($LASTEXITCODE -ne 0) { throw 'Release verification failed' }
Write-Host "Release: $releaseArchive"
