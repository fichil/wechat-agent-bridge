[CmdletBinding()]
param([switch]$NoBrowser, [switch]$Check, [switch]$SmokeTest)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv-backend\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv-backend. Follow the installation instructions in README.md.'
}
$launchArgs = @('-B', '-X', 'utf8', (Join-Path $PSScriptRoot 'dev_server.py'))
if ($NoBrowser) { $launchArgs += '--no-browser' }
if ($Check) { $launchArgs += '--check' }
if ($SmokeTest) { $launchArgs += '--smoke-test' }
& $pythonPath @launchArgs
exit $LASTEXITCODE
