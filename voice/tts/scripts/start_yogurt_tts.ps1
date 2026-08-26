[CmdletBinding()]
param([string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) '.env'))

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "Create a local .env from .env.example first: $EnvFile" }
Get-Content -LiteralPath $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') { Set-Item -Path "Env:$($matches[1].Trim())" -Value $matches[2].Trim() }
}
if (-not $env:YOGURT_TTS_PYTHON) { $env:YOGURT_TTS_PYTHON = 'python' }
$ttsRoot = Split-Path -Parent $PSScriptRoot
Push-Location (Split-Path -Parent $ttsRoot)
try { & $env:YOGURT_TTS_PYTHON -m tts.runtime.app } finally { Pop-Location }
