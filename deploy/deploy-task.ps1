[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$controller = Join-Path $InstallRoot 'controller'
$log = Join-Path $InstallRoot 'deploy-task.log'
& (Join-Path $controller 'runtime\python.exe') -B -X utf8 (Join-Path $controller 'deploy.py') apply --root $InstallRoot >> $log 2>&1
exit $LASTEXITCODE
