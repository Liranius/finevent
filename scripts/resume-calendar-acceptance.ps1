param([string]$RunId, [string]$Commit, [string]$ReportSha256, [string]$ConfigPath)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$sshTarget = Initialize-FineventDeployment -ConfigPath $ConfigPath
if ($RunId -cnotmatch '^calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}$' -or
    $Commit -cnotmatch '^[a-f0-9]{40}$' -or $ReportSha256 -cnotmatch '^[a-f0-9]{64}$') {
    throw 'Supply the registered RunId, full Commit and interrupted ReportSha256.'
}
if (-not $sshTarget.StartsWith('root@')) { throw 'Use the existing administrative SSH login for acceptance recovery.' }
$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $root ('.validation/acceptance-tools/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
foreach ($name in @('resume_calendar_acceptance.py','calendar_release.py','persistence_baseline.py','calendar_review.py','trend_settings.py','install.py','web_release.py','memory_recovery.py','memory_diagnostic.py')) {
    Export-FineventDeploymentFile (Join-Path "$root/deploy" $name) (Join-Path $stage $name)
}
Copy-Item -LiteralPath (Join-Path $root 'deploy/calendar-reviews.json') -Destination $stage
$archive = "$stage.tar.gz"
& tar -czf $archive -C $stage .
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare acceptance tools' }
# The controller key only supports its fixed protocol. Admin SSH prompts in the
# operator's terminal; stdin carries only this small bundle of verification tools.
$remote = 'umask 077; d=$(mktemp -d /var/lib/finevent-release/acceptance-tools.XXXXXXXX) && tar -xzf - -C "$d" && python3 -B "$d/resume_calendar_acceptance.py"'
$remote += " --run-id $RunId --commit $Commit --report-sha256 $ReportSha256"
& python -c 'import subprocess,sys; f=open(sys.argv[1],"rb"); sys.exit(subprocess.run(sys.argv[2:],stdin=f).returncode)' $archive ssh -o StrictHostKeyChecking=yes $sshTarget $remote
if ($LASTEXITCODE -ne 0) { throw 'Recovery launch was not confirmed. Query the same operation before retrying.' }
Write-Host "Verification requested for $RunId. Follow this same operation with Report."
