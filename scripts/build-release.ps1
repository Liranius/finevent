param([switch]$Generic, [switch]$SkipFrontendBuild)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
. (Join-Path $PSScriptRoot 'release-archive.ps1')
# Preserve the local entrypoint's early configuration validation.
if (-not $Generic) { $null = Initialize-FineventDeployment }
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not $SkipFrontendBuild) {
    & pnpm build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
}
$sourceCommit = & git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify source commit' }
$sourceChanges = & git status --porcelain --untracked-files=normal
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify source changes' }
$releaseStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$releaseRoot = Join-Path (Get-Location) ('.validation/release-builds/' + [Guid]::NewGuid().ToString('N'))
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
Copy-Item -LiteralPath 'apps/web/dist' -Destination $releaseRoot -Recurse -Force
$releaseDeploy = Join-Path $releaseRoot 'deploy'
New-Item -ItemType Directory -Path $releaseDeploy | Out-Null
Copy-Item -LiteralPath 'apps/server/internal/calendar/snapshots/nbs-2026.json' -Destination (Join-Path $releaseDeploy 'calendar-snapshot.json')
foreach ($deployFile in @('install.py','backup.py','calendar_release.py','persistence_baseline.py','news_release.py','news_push_release.py','source_health_release.py','calendar_links_release.py','ai_release.py','calendar_review.py','calendar-reviews.json','verification.py','verification_resources.py','memory_recovery.py','memory_diagnostic.py','finevent.service','finevent-backup.service','finevent-backup.timer','finevent.nginx.conf')) {
    Copy-Item -LiteralPath (Join-Path 'deploy' $deployFile) -Destination (Join-Path $releaseDeploy $deployFile)
}
Copy-Item -LiteralPath '.deployment.example.json' -Destination (Join-Path $releaseRoot 'deployment-template.json')
@{ schema=1; kind='generic'; target='linux/amd64'; source_commit=$sourceCommit.Trim(); source_dirty=[bool]$sourceChanges; created_utc=[DateTime]::UtcNow.ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot 'release.json') -Encoding utf8
$releaseArchive = Join-Path (Get-Location) "release/generic/finevent-$releaseStamp.tar.gz"
New-FineventReleaseArchive -SourceRoot $releaseRoot -Archive $releaseArchive -Kind generic
if (-not $Generic) { & (Join-Path $PSScriptRoot 'render-release.ps1') -Archive $releaseArchive }
