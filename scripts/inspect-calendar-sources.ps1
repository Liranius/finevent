param([string]$SshTarget, [switch]$Report, [switch]$Candidate)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$latest = '.validation/calendar-source-check-latest.json'
if ($Report) {
    if ($Candidate) { throw '-Report reads the existing report; do not combine it with -Candidate' }
    $operation = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
    $runId = $operation.id
} else {
    $runId = 'calendar-source-check-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
}
if ($runId -notmatch '^calendar-source-check-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid operation identity' }
$localDirectory = Join-Path (Get-Location) ".validation/$runId"
$remoteDirectory = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$runId")
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
if (-not $Report) {
    $oldGoOS = $env:GOOS
    $oldGoArch = $env:GOARCH
    $oldCgo = $env:CGO_ENABLED
    try {
        $env:GOOS = 'linux'; $env:GOARCH = 'amd64'; $env:CGO_ENABLED = '0'
        & go -C apps/server build -trimpath '-ldflags=-s -w' -o (Join-Path $localDirectory 'source-check') ./cmd/calendar-source-check
        if ($LASTEXITCODE -ne 0) { throw 'Diagnostic build failed; nothing uploaded' }
    } finally {
        $env:GOOS = $oldGoOS; $env:GOARCH = $oldGoArch; $env:CGO_ENABLED = $oldCgo
    }
    @{ id=$runId; candidate=[bool]$Candidate; remote_report="$remoteDirectory/report.json" } | ConvertTo-Json | Set-Content -LiteralPath $latest -Encoding utf8
    $diagnosticArgument = ''
    $runtimeLimit = 100
    if ($Candidate) {
        $diagnosticArgument = '-candidate'
        $runtimeLimit = 210
        Write-Host 'Candidate diagnosis: two NBS URLs with a 60-second limit and one New York Fed calendar page. Does not retry BLS or install the candidate.'
    } else {
        Write-Host 'Baseline diagnosis: four official URLs, same 15-second client limit.'
    }
    Write-Host 'No database, deployment, service restart or AI call. Each run preserves its own report.'
    Invoke-FineventScp -o StrictHostKeyChecking=yes (Join-Path $localDirectory 'source-check') "${SshTarget}:/tmp/$runId"
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic upload failed' }
    Write-Host 'The diagnostic continues on the server if SSH disconnects. Waiting up to 65 seconds for its report.'
    Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 $SshTarget "mkdir -m 700 '$remoteDirectory' && install -m 700 '/tmp/$runId' '$remoteDirectory/source-check' && systemd-run --unit='$runId' --property=MemoryMax=64M --property=MemorySwapMax=0 --property=RuntimeMaxSec=$runtimeLimit /bin/sh -c 'GOMEMLIMIT=40MiB GOMAXPROCS=1 $remoteDirectory/source-check $diagnosticArgument > $remoteDirectory/report.tmp && mv $remoteDirectory/report.tmp $remoteDirectory/report.json' && for n in 1 2 3 4 5 6 7 8 9 10 11 12 13; do test -f '$remoteDirectory/report.json' && exit 0; sleep 5; done; exit 3"
    if ($LASTEXITCODE -eq 3) {
        Write-Host 'Still running or report not ready. After two minutes use .\scripts\inspect-calendar-sources.ps1 -Report to retrieve this run; do not relaunch.'
        return
    }
    if ($LASTEXITCODE -ne 0) { throw "Launch or report needs review. Preserve $remoteDirectory and use .\scripts\inspect-calendar-sources.ps1 -Report; do not relaunch blindly." }
}
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteDirectory/report.json" (Join-Path $localDirectory 'report.json')
if ($LASTEXITCODE -ne 0) { throw "Report download failed; preserve $remoteDirectory and use -Report." }
Write-Host "Report: $localDirectory/report.json"
