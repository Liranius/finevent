$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('finevent-deployment-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
$previousConfig = $env:FINEVENT_DEPLOY_CONFIG
$previousLocation = Get-Location
function Assert-Deployment($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Assert-DeploymentFailure($Operation, $Pattern) {
    try { & $Operation } catch {
        if ($_.Exception.Message -match $Pattern) { return }
        throw
    }
    throw "Expected deployment failure: $Pattern"
}
try {
    $env:FINEVENT_DEPLOY_CONFIG = Join-Path $temporary 'missing.json'
    $entrypoints = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' |
        Where-Object { $_.Name -notin @('deployment-config.ps1', 'test-deployment-config.ps1') -and ((Get-Content -LiteralPath $_.FullName -Raw) -match '\$SshTarget' -or $_.Name -eq 'build-release.ps1') }
    foreach ($entry in $entrypoints) {
        Assert-DeploymentFailure { & $entry.FullName } 'Create .deployment.local.json'
    }
    $settings = Get-Content -LiteralPath (Join-Path $repoRoot '.deployment.example.json') -Raw | ConvertFrom-Json -AsHashtable
    $settings.sshTarget = 'operator@node.invalid'
    $settings.siteHost = 'portal.invalid'
    $settings.siteAlias = 'alias.invalid'
    foreach ($key in @('appRoot','dataRoot','backupRoot','trendradarRoot','hostStateRoot','legacyRoot','sshStageRoot','nginxSnippet')) {
        $settings[$key] = '/private-test/' + $key
    }
    $env:FINEVENT_DEPLOY_CONFIG = Join-Path $temporary 'deployment.json'
    $settings | ConvertTo-Json | Set-Content -LiteralPath $env:FINEVENT_DEPLOY_CONFIG -Encoding utf8
    $target = Initialize-FineventDeployment
    Assert-Deployment ($target -eq 'operator@node.invalid') 'Local SSH target was not loaded'
    Assert-Deployment ((Initialize-FineventDeployment -SshTarget 'override@node.invalid') -eq 'override@node.invalid') 'Explicit target override failed'
    $null = Initialize-FineventDeployment
    $rendered = ConvertTo-FineventDeploymentText 'https://www.example.com /srv/example/finevent-data/db /srv/example/finevent/current'
    Assert-Deployment ($rendered -eq 'https://portal.invalid /private-test/dataRoot/db /private-test/appRoot/current') 'Overlapping deployment paths rendered incorrectly'
    foreach ($file in @('finevent.service','finevent.nginx.conf','install.py','calendar_release.py','backup.py')) {
        $destination = Join-Path $temporary $file
        Export-FineventDeploymentFile (Join-Path $repoRoot "deploy/$file") $destination
        $content = Get-Content -LiteralPath $destination -Raw
        Assert-Deployment ($content -notmatch 'www.example.com|/srv/example/') "Unrendered deployment source: $file"
    }
    $stub = Join-Path $temporary 'native.ps1'
    '@($args) | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:FINEVENT_TEST_ARGUMENTS; exit 17' | Set-Content -LiteralPath $stub
    $env:FINEVENT_TEST_ARGUMENTS = Join-Path $temporary 'arguments.json'
    function Get-Command { [pscustomobject]@{ Source = $stub } }
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $target "cat '/srv/example/finevent-data/config.json'"
    $arguments = Get-Content -LiteralPath $env:FINEVENT_TEST_ARGUMENTS -Raw | ConvertFrom-Json
    Assert-Deployment ($LASTEXITCODE -eq 17 -and $arguments[0] -eq '-t' -and $arguments[-1] -eq "cat '/private-test/dataRoot/config.json'") 'SSH argument boundaries or exit code changed'
    Invoke-FineventScp -o StrictHostKeyChecking=yes (Join-Path $repoRoot 'deploy/install.py') "${target}:/srv/example/staging/"
    $arguments = Get-Content -LiteralPath $env:FINEVENT_TEST_ARGUMENTS -Raw | ConvertFrom-Json
    Assert-Deployment ($LASTEXITCODE -eq 17 -and $arguments[-1] -eq 'operator@node.invalid:/private-test/sshStageRoot/') 'SCP destination or exit code changed'
    Assert-Deployment ((Get-Content -LiteralPath $arguments[-2] -Raw) -match 'portal.invalid') 'SCP uploaded an unrendered source'
    function python { @($args) | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:FINEVENT_TEST_ARGUMENTS; $global:LASTEXITCODE = 0 }
    & (Join-Path $PSScriptRoot 'publish-news.ps1') -Archive 'fixture.tar.gz' -AfterReview 'previous' -BaselineId 'baseline' -BaselineSha256 'baseline-hash' -RecoverPreflight 'failed' -FailedReportSha256 'failed-hash'
    $arguments = Get-Content -LiteralPath $env:FINEVENT_TEST_ARGUMENTS -Raw | ConvertFrom-Json
    foreach ($pair in @(@('--baseline-id','baseline'), @('--baseline-sha256','baseline-hash'), @('--recover-preflight','failed'), @('--failed-report-sha256','failed-hash'))) {
        $position = [Array]::IndexOf($arguments, $pair[0])
        Assert-Deployment ($position -ge 0 -and $arguments[$position+1] -eq $pair[1]) 'Recovery option argument boundary changed'
    }
    & (Join-Path $PSScriptRoot 'verify-baseline.ps1') -Archive 'fixture.tar.gz'
    $arguments = Get-Content -LiteralPath $env:FINEVENT_TEST_ARGUMENTS -Raw | ConvertFrom-Json
    Assert-Deployment ($arguments[1] -eq 'baseline' -and $arguments -notcontains 'deploy') 'Read-only baseline selected the deployment action'
    Remove-Item Function:python
    foreach ($invalid in @(@('sshTarget','user@host;bad'), @('siteHost','host.invalid;bad'), @('dataRoot','/tmp/../etc'), @('dataRoot','/tmp/a b'))) {
        $bad = $settings.Clone(); $bad[$invalid[0]] = $invalid[1]
        $bad | ConvertTo-Json | Set-Content -LiteralPath $env:FINEVENT_DEPLOY_CONFIG -Encoding utf8
        Assert-DeploymentFailure { $null = Initialize-FineventDeployment } 'Invalid'
    }
    Write-Host "Deployment configuration checks passed: $($entrypoints.Count) entrypoints, rendering, native arguments, exit codes and invalid settings."
} finally {
    Remove-Item Function:python -ErrorAction SilentlyContinue
    Remove-Item Function:Get-Command -ErrorAction SilentlyContinue
    $env:FINEVENT_DEPLOY_CONFIG = $previousConfig
    Remove-Item Env:FINEVENT_TEST_ARGUMENTS -ErrorAction SilentlyContinue
    Set-Location -LiteralPath $previousLocation.Path
    $resolved = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected test temporary path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
