[CmdletBinding()]
param([switch]$Check, [switch]$Stop, [switch]$Foreground)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv-backend\Scripts\python.exe'
$scriptPath = Join-Path $PSScriptRoot 'production_server.py'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install backend dependencies first.' }
$launchArgs = @('-B', '-X', 'utf8', $scriptPath)
if ($Check) { $launchArgs += '--check' }
if ($Stop) { $launchArgs += '--stop' }
if ($Check -or $Stop -or $Foreground) {
    & $pythonPath @launchArgs
    exit $LASTEXITCODE
}
& $pythonPath @launchArgs --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$runtimeDirectory = Join-Path $projectRoot '.local\production'
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
$supervisorLog = Join-Path $runtimeDirectory ('supervisor-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$quotedScript = '"' + $scriptPath + '"'
$supervisor = Start-Process -FilePath $pythonPath -ArgumentList @('-B', '-X', 'utf8', $quotedScript) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput ($supervisorLog + '.out.log') -RedirectStandardError ($supervisorLog + '.err.log')
Write-Output "Supervisor started (PID $($supervisor.Id)); verify the HTTPS login page and local production logs."
