param([Parameter(Mandatory)][string]$Archive, [string]$OutputDirectory, [string]$ConfigPath)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
. (Join-Path $PSScriptRoot 'release-archive.ps1')
$repoRoot = Split-Path -Parent $PSScriptRoot
$Archive = (Resolve-Path -LiteralPath $Archive).Path
$archiveName = Split-Path -Leaf $Archive
if ($archiveName -notmatch '^finevent-\d{8}T\d{6}Z\.tar\.gz$') { throw 'Unexpected release filename' }
& python (Join-Path $PSScriptRoot 'check-release.py') $Archive --kind generic
if ($LASTEXITCODE -ne 0) { throw 'Generic release verification failed; nothing rendered' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'release' }
$destination = Join-Path ([IO.Path]::GetFullPath($OutputDirectory, (Get-Location).Path)) $archiveName
if (Test-Path -LiteralPath $destination) { throw 'Release archive already exists; preserve it and choose another output directory' }
$stage = Join-Path $repoRoot ('.validation/release-rendered/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage -Force | Out-Null
# The checker rejects links, duplicate paths and traversal before extraction.
& tar -xzf $Archive -C $stage
if ($LASTEXITCODE -ne 0) { throw 'Generic release extraction failed' }
$template = Join-Path $stage 'deployment-template.json'
$null = Initialize-FineventDeployment -ConfigPath $ConfigPath -TemplatePath $template
foreach ($file in (Get-ChildItem -LiteralPath (Join-Path $stage 'deploy') -File)) {
    if ($file.Extension -in @('.py','.service','.timer','.conf','.json') -and $file.Name -ne 'calendar-snapshot.json') {
        Export-FineventDeploymentFile $file.FullName $file.FullName
    }
}
Remove-Item -LiteralPath $template
$metadataPath = Join-Path $stage 'release.json'
$metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json -AsHashtable
$metadata.kind = 'deployment'
$metadata.generic_sha256 = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
$metadata | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding utf8
New-FineventReleaseArchive -SourceRoot $stage -Archive $destination -Kind deployment
