function New-FineventReleaseArchive {
    param([string]$SourceRoot, [string]$Archive, [ValidateSet('generic','deployment')][string]$Kind)
    if (Test-Path -LiteralPath $Archive) { throw 'Release archive already exists; preserve it and choose another output path' }
    $manifest = @{}
    # Vite emits .vite/manifest.json; Unix dotfiles must also be checksummed.
    foreach ($file in (Get-ChildItem -LiteralPath $SourceRoot -File -Recurse -Force)) {
        $relative = [IO.Path]::GetRelativePath($SourceRoot, $file.FullName).Replace([IO.Path]::DirectorySeparatorChar, [char]'/')
        if ($relative -ne 'sha256.json') { $manifest[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $SourceRoot 'sha256.json') -Encoding utf8
    New-Item -ItemType Directory -Path (Split-Path -Parent $Archive) -Force | Out-Null
    & tar -czf $Archive -C $SourceRoot .
    if ($LASTEXITCODE -ne 0) { throw 'Archive creation failed' }
    & python (Join-Path $PSScriptRoot 'check-release.py') $Archive --kind $Kind
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed' }
    $digest = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$digest  $(Split-Path -Leaf $Archive)" | Set-Content -LiteralPath "$Archive.sha256" -Encoding utf8NoBOM
    Write-Host "Release ($Kind): $Archive"
}
