param([string]$SshTarget, [switch]$Workflow)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
$audit = Get-Content -LiteralPath '.validation/trendradar-audit-latest.json' -Raw | ConvertFrom-Json
if ($audit.id -notmatch '^trendradar-audit-\d{8}T\d{6}Z-[a-f0-9]{8}$') { throw 'Invalid audit ID' }
$probeKind = if ($Workflow) { 'workflow' } else { 'import' }
$probeId = "$probeKind-probe-" + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$remoteStage = ConvertTo-FineventDeploymentText ("/srv/example/staging/finevent-$probeId")
$remoteReport = ConvertTo-FineventDeploymentText ("/srv/example/finevent-backups/$probeId/report.json")
$localDirectory = Join-Path (Get-Location) ".validation/$probeId"
$localReport = Join-Path $localDirectory 'report.json'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
@{ id=$probeId; remote_report=$remoteReport; local_report=$localReport; audit_id=$audit.id } |
    ConvertTo-Json | Set-Content -LiteralPath ".validation/$probeKind-probe-latest.json" -Encoding utf8
if ($Workflow) {
    Write-Host 'Compare two synthetic collection rounds, SQLite and HTML in original/lazy offline containers.'
} else {
    Write-Host 'Measure baseline/original/lazy imports in five sequential offline, bounded temporary containers.'
}
Write-Host 'Production configuration, news data and HTML are not mounted or changed. Avoid the minutes around :00 and :30.'
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes $SshTarget "mkdir -m 700 '$remoteStage'"
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the probe upload directory' }
$probeFiles = @('deploy/trendradar_import_probe.py', 'deploy/memory_recovery.py', 'deploy/memory_diagnostic.py')
if ($Workflow) { $probeFiles += 'deploy/trendradar_workflow_fixture.py' }
Invoke-FineventScp -o StrictHostKeyChecking=yes @probeFiles "${SshTarget}:$remoteStage/"
if ($LASTEXITCODE -ne 0) { throw 'Probe upload failed' }
# Password authentication stays interactive; never transcript or redirect this SSH session.
$workflowArgument = if ($Workflow) { ' --workflow' } else { '' }
Invoke-FineventSsh -t -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 $SshTarget "python3 -B '$remoteStage/trendradar_import_probe.py' --run-id '$probeId' --audit-id '$($audit.id)'$workflowArgument"
$probeExit = $LASTEXITCODE
Write-Host 'Enter the server password again when prompted to download the report.'
Invoke-FineventScp -o StrictHostKeyChecking=yes "${SshTarget}:$remoteReport" $localReport
$reportExit = $LASTEXITCODE
Write-Host "Probe exit: $probeExit; report download exit: $reportExit"
if ($probeExit -ne 0 -or $reportExit -ne 0) { throw "Probe incomplete; retain $remoteReport for review before retrying." }
Write-Host "Report downloaded: $localReport"
