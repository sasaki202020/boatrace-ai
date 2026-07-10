param(
    [switch]$UseLocalOllama,
    [string]$Model = "qwen2.5-coder:7b",
    [string]$BaseUrl = "http://localhost:11434",
    [string]$WorkingDirectory = (Split-Path -Parent $PSScriptRoot),
    [switch]$AutoInstallClaude = $true,
    [switch]$AutoInstallOllama = $true
)

$ErrorActionPreference = "Stop"

function Assert-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )

    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is not available. $InstallHint"
    }
}

function Install-ClaudeCodeIfNeeded {
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        return $true
    }

    if (-not $AutoInstallClaude) {
        return $false
    }

    Write-Host "Claude Code is missing. Installing it now..." -ForegroundColor Yellow
    & ([scriptblock]::Create((irm https://claude.ai/install.ps1)))

    if (Get-Command claude -ErrorAction SilentlyContinue) {
        return $true
    }

    return $false
}

function Resolve-ClaudeExecutable {
    $localBinPath = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
    if (Test-Path $localBinPath) {
        return $localBinPath
    }

    $fromCommand = Get-Command claude -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
    if ($fromCommand -and ($fromCommand -is [string]) -and $fromCommand.Trim().ToLower().EndsWith(".exe") -and (Test-Path $fromCommand)) {
        return $fromCommand
    }

    $directPath = Join-Path $env:LOCALAPPDATA "AnthropicClaude\claude.exe"
    if (Test-Path $directPath) {
        return $directPath
    }

    $appDir = Join-Path $env:LOCALAPPDATA "AnthropicClaude"
    if (Test-Path $appDir) {
        $latest = Get-ChildItem -Path $appDir -Filter claude.exe -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($latest) {
            return $latest.FullName
        }
    }

    return $null
}

function Invoke-Executable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    if (!(Test-Path $FilePath)) {
        throw "Executable not found: $FilePath"
    }

    & "$FilePath" @Arguments
}

function Install-OllamaIfNeeded {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        return $true
    }

    if (-not $AutoInstallOllama) {
        return $false
    }

    Write-Host "Ollama is missing. Installing it now..." -ForegroundColor Yellow
    & ([scriptblock]::Create((irm https://ollama.com/install.ps1)))

    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        return $true
    }

    return $false
}

function Resolve-OllamaExecutable {
    $candidates = @(
        (Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:LOCALAPPDATA\Ollama\ollama.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }

    if ($candidates.Count -gt 0) {
        return [string]$candidates[0]
    }

    foreach ($dir in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama"),
        (Join-Path $env:LOCALAPPDATA "Ollama")
    )) {
        if (Test-Path $dir) {
            $match = Get-ChildItem -Path $dir -Filter ollama.exe -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($match) {
                return $match.FullName
            }
        }
    }

    return $null
}

if (-not (Install-ClaudeCodeIfNeeded)) {
    throw "claude is not available. Install Claude Code first: irm https://claude.ai/install.ps1 | iex"
}

Set-Location $WorkingDirectory

$claudeExe = Resolve-ClaudeExecutable
if (-not $claudeExe) {
    throw "Claude Code was installed, but claude.exe could not be located under $env:LOCALAPPDATA\AnthropicClaude."
}

if ($UseLocalOllama) {
    if (-not (Install-OllamaIfNeeded)) {
        throw "ollama is not available. Install Ollama first: irm https://ollama.com/install.ps1 | iex"
    }
    $ollamaExe = Resolve-OllamaExecutable
    if (-not $ollamaExe) {
        throw "Ollama was installed, but ollama.exe could not be located."
    }
    $env:ANTHROPIC_AUTH_TOKEN = "ollama"
    $env:ANTHROPIC_BASE_URL = $BaseUrl
    Write-Host "Using local Ollama endpoint at $BaseUrl" -ForegroundColor Cyan
    Write-Host "Model: $Model" -ForegroundColor Cyan
    Invoke-Executable -FilePath $ollamaExe -Arguments @("list") | Out-Null
    Invoke-Executable -FilePath $claudeExe -Arguments @("--model", $Model)
    return
}

Write-Host "Launching Claude Code in $WorkingDirectory" -ForegroundColor Cyan
Invoke-Executable -FilePath $claudeExe
