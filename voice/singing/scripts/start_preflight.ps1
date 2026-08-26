[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Config)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = $env:YOGURT_SINGING_PYTHON
if (-not $python) { $python = 'python' }
& $python (Join-Path $PSScriptRoot 'preflight.py') --config $Config
