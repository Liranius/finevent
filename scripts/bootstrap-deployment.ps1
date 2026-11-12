param([string]$SshTarget, [switch]$PrepareOnly, [string]$KnownHosts)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deployment-config.ps1')
$SshTarget = Initialize-FineventDeployment -SshTarget $SshTarget
if (-not $SshTarget.StartsWith('root@')) { throw 'Bootstrap requires the existing root login' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$access = Join-Path $repoRoot '.validation/deploy-access'
New-Item -ItemType Directory -Path $access -Force | Out-Null
# The unencrypted key is dedicated to unattended deployment. Restrict its local storage.
if ($IsWindows) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls $access /inheritance:r /grant:r "*${sid}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot secure deployment credential directory' }
    $rules = (Get-Acl -LiteralPath $access).GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
    if (@($rules | Where-Object { $_.IdentityReference.Value -ne $sid }).Count) { throw 'Deployment credential directory grants another identity access; review its ACL first' }
} else {
    & chmod 700 $access
    if ($LASTEXITCODE -ne 0) { throw 'Cannot secure deployment credential directory' }
}
$key = Join-Path $access 'id_ed25519'
if (-not (Test-Path -LiteralPath $key)) {
    & ssh-keygen -q -t ed25519 -N '' -C 'finevent-release' -f $key
    if ($LASTEXITCODE -ne 0) { throw 'Cannot generate dedicated deployment key' }
}
if (-not (Test-Path -LiteralPath "$key.pub")) { throw 'Public key missing; preserve the private key and repair the pair' }
$derived = & ssh-keygen -y -P '' -f $key
if ($LASTEXITCODE -ne 0 -or ($derived -split ' ')[1] -ne ((Get-Content -LiteralPath "$key.pub" -Raw) -split ' ')[1]) { throw 'Deployment key pair does not match' }
if (-not $KnownHosts) { $KnownHosts = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh/known_hosts' }
if (-not (Test-Path -LiteralPath $KnownHosts -PathType Leaf)) { throw 'A previously verified known_hosts file is required; do not trust ssh-keyscan blindly' }
$hostName = ($SshTarget -split '@', 2)[1]
$records = @(& ssh-keygen -F $hostName -f $KnownHosts | Where-Object { $_ -and -not $_.StartsWith('#') })
if ($LASTEXITCODE -ne 0 -or $records.Count -eq 0) { throw 'No trusted fingerprint for this host; verify it through the server console first' }
$pinned = Join-Path $access 'known_hosts'
[IO.File]::WriteAllText($pinned, (($records -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
if (-not $IsWindows) { & chmod 600 $key $pinned }
$stage = Join-Path $access 'bootstrap'
New-Item -ItemType Directory -Path $stage -Force | Out-Null
foreach ($name in @('release_control.py', 'bootstrap_release.py')) {
    Copy-Item -LiteralPath (Join-Path $repoRoot "deploy/$name") -Destination $stage -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'check-release.py') -Destination $stage -Force
Copy-Item -LiteralPath "$key.pub" -Destination (Join-Path $stage 'deploy.pub') -Force
@{ appRoot=(ConvertTo-FineventDeploymentText '/srv/example/finevent'); dataRoot=(ConvertTo-FineventDeploymentText '/srv/example/finevent-data'); backupRoot=(ConvertTo-FineventDeploymentText '/srv/example/finevent-backups') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage 'config.json') -Encoding utf8
$archive = Join-Path $access 'bootstrap.tar.gz'
& tar -czf $archive -C $stage .
if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare bootstrap archive' }
Write-Host 'Prepared dedicated key and pinned known_hosts under .validation/deploy-access (ignored by Git).'
if ($PrepareOnly) { return }
Write-Host 'Enter the existing SSH password only in this terminal. This installs the release entrypoint without restarting the application.'
& python (Join-Path $PSScriptRoot 'release-client.py') bootstrap --target $SshTarget --known-hosts $pinned --archive $archive
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap did not complete; retain the prepared key when retrying' }
& python (Join-Path $PSScriptRoot 'release-client.py') preflight --target $SshTarget --identity $key --known-hosts $pinned --output (Join-Path $access 'preflight.json')
if ($LASTEXITCODE -ne 0) { throw 'Restricted-key preflight failed; review before deployment' }
Write-Host 'Initialization and read-only preflight completed. No release was launched.'
