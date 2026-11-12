param(
    [string]$SshTarget,
    [switch]$EnableSwapPolicy
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$trialId = 'memory-high-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$trialId")
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$trialId/report.json")
$localDirectory = Join-Path (Get-Location) ".validation/$trialId"
$localReport = Join-Path $localDirectory 'report.json'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
@{ id=$trialId; remote_report=$remoteReport; local_report=$localReport; enable_swap_policy=[bool]$EnableSwapPolicy } |
    ConvertTo-Json | Set-Content -LiteralPath '.validation/memory-high-latest.json' -Encoding utf8
Write-Host 'Try TrendRadar early reclaim at 160 MiB, keeping the 192 MiB RAM and 256 MiB swap caps.'
$swapArgument = ''
if ($EnableSwapPolicy) {
    $swapArgument = ' --enable-swap-policy'
    Write-Host 'Also try host vm.swappiness=60 at runtime. Failure restores its previous value; no boot configuration is written.'
}
Write-Host 'Observe two normal cycles (about 35-65 minutes). Keep the terminal open until the report download completes.'
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare upload directory; no resource policy changed' }
Invoke-FineventScp -o StrictHostKeyChecking=yes 'deploy/memory_high_trial.py' 'deploy/memory_recovery.py' 'deploy/memory_diagnostic.py' "${SshTarget}:$remoteStage/"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed; no trial started' }
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 $SshTarget "python3 '$remoteStage/memory_high_trial.py' --run-id '$trialId'$swapArgument"
$trialExit = $LASTEXITCODE
Write-Host 'The server stage ended. Enter the SSH password again if prompted to download the report.'
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localReport
$reportExit = $LASTEXITCODE
Write-Host "Trial exit: $trialExit; report download exit: $reportExit"
if ($trialExit -ne 0 -or $reportExit -ne 0 -or -not (Test-Path -LiteralPath $localReport -PathType Leaf)) {
    throw "Trial needs review. Evidence stays at $remoteReport; do not rerun the original swap-creation script."
}
Write-Host "Report downloaded: $localReport"
