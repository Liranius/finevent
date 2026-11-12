# The public deployment sources use the values in .deployment.example.json.
# Actual addresses and paths are rendered only into ignored local artifacts.
function Initialize-FineventDeployment {
    param([string]$SshTarget, [string]$ConfigPath, [string]$TemplatePath)
    $repoRoot = Split-Path -Parent $PSScriptRoot
    if (-not $ConfigPath) { $ConfigPath = $env:FINEVENT_DEPLOY_CONFIG }
    if (-not $ConfigPath) { $ConfigPath = Join-Path $repoRoot '.deployment.local.json' }
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw 'Create .deployment.local.json from .deployment.example.json and fill in the deployment settings.'
    }
    if (-not $TemplatePath) { $TemplatePath = Join-Path $repoRoot '.deployment.example.json' }
    $template = Get-Content -LiteralPath $TemplatePath -Raw | ConvertFrom-Json -AsHashtable
    $settings = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json -AsHashtable
    foreach ($name in $template.Keys) {
        $value = $settings[$name]
        if ($value -isnot [string] -or -not $value) { throw "Missing deployment setting: $name" }
        if ($name -eq 'sshTarget') {
            if ($value -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid deployment SSH target' }
        } elseif ($name -in @('siteHost', 'siteAlias')) {
            if ($value -notmatch '^[a-zA-Z0-9.-]+$' -or $value.StartsWith('-')) { throw "Invalid deployment hostname: $name" }
        } elseif ($value -notmatch '^/[a-zA-Z0-9_./-]+$' -or $value.Contains('//') -or $value.Split('/') -contains '..' -or $value.EndsWith('/')) {
            throw "Invalid absolute deployment path: $name"
        }
    }
    if ($SshTarget) {
        if ($SshTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$') { throw 'Invalid SSH target' }
        $settings.sshTarget = $SshTarget
    }
    $script:FineventReplacements = @{}
    foreach ($name in $template.Keys) { $script:FineventReplacements[$template[$name]] = $settings[$name] }
    $patterns = $script:FineventReplacements.Keys | Sort-Object Length -Descending | ForEach-Object { [regex]::Escape($_) }
    $script:FineventTemplatePattern = [regex]::new(($patterns -join '|'))
    $script:FineventRenderRoot = Join-Path $repoRoot '.validation/deployment-rendered'
    return $settings.sshTarget
}

function ConvertTo-FineventDeploymentText {
    param([string]$Text)
    if (-not $script:FineventTemplatePattern) { throw 'Initialize deployment settings first' }
    # A single replacement pass prevents one configured path being replaced again.
    return $script:FineventTemplatePattern.Replace($Text, [System.Text.RegularExpressions.MatchEvaluator]{
        param($match)
        return $script:FineventReplacements[$match.Value]
    })
}

function Export-FineventDeploymentFile {
    param([string]$Source, [string]$Destination)
    $content = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $Source))
    [IO.File]::WriteAllText($Destination, (ConvertTo-FineventDeploymentText $content), [Text.UTF8Encoding]::new($false))
}

function Invoke-FineventSsh {
    $native = (Get-Command ssh -CommandType Application -ErrorAction Stop).Source
    $arguments = @($args | ForEach-Object { ConvertTo-FineventDeploymentText ([string]$_) })
    & $native @arguments
    $global:LASTEXITCODE = $LASTEXITCODE
}

function Invoke-FineventScp {
    $native = (Get-Command scp -CommandType Application -ErrorAction Stop).Source
    $arguments = foreach ($argument in $args) {
        $value = [string]$argument
        if ((Test-Path -LiteralPath $value -PathType Leaf) -and [IO.Path]::GetExtension($value) -in @('.py', '.service', '.timer', '.conf')) {
            $directory = Join-Path $script:FineventRenderRoot ([Guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            $rendered = Join-Path $directory ([IO.Path]::GetFileName($value))
            Export-FineventDeploymentFile $value $rendered
            $rendered
        } else {
            ConvertTo-FineventDeploymentText $value
        }
    }
    & $native @arguments
    $global:LASTEXITCODE = $LASTEXITCODE
}
