[CmdletBinding()]
param(
    [string]$Config = "E:\Yogurt_Singing_Runtime\pipeline.json",
    [switch]$Resume,
    [switch]$KeepWork,
    [switch]$SkipMix,
    [switch]$SeparationOnly,
    [switch]$SkipPlaylistPreprocess
)

$ErrorActionPreference = 'Stop'
if (!(Test-Path -LiteralPath $Config)) { throw "Pipeline config not found: $Config" }
$data = Get-Content -Raw -LiteralPath $Config | ConvertFrom-Json
$incoming = [string]$data.incoming_dir
New-Item -ItemType Directory -Force -Path $incoming | Out-Null
$runtimePython = [string]$data.seed_vc.runtime_config | Split-Path -Parent
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script = Join-Path $PSScriptRoot 'run_pipeline.py'
$preprocessScript = Join-Path $PSScriptRoot 'prepare_playlist_input.py'
$python = 'E:\Yogurt_Singing_Runtime\.venv\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw "Stable runtime Python not found: $python" }

if (!$SkipPlaylistPreprocess) {
    if (!(Test-Path -LiteralPath $preprocessScript)) { throw "Playlist preprocessor not found: $preprocessScript" }
    & $python $preprocessScript '--config' $Config
    $preprocessExit = $LASTEXITCODE
    if ($preprocessExit -ne 0) {
        throw "Playlist preprocessing failed with exit code $preprocessExit"
    }
}

$arguments = @($script, '--config', $Config)
if ($Resume) { $arguments += '--resume' }
if ($KeepWork) { $arguments += '--keep-work' }
if ($SkipMix) { $arguments += '--skip-mix' }
if ($SeparationOnly) { $arguments += '--separation-only' }
& $python @arguments
exit $LASTEXITCODE
