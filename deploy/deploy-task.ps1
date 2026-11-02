[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$controller = Join-Path $InstallRoot 'controller'
$logDirectory = Join-Path $InstallRoot 'task-logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logBase = Join-Path $logDirectory ('deploy-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$arguments = @('-B','-X','utf8',('"' + (Join-Path $controller 'deploy.py') + '"'),'apply','--root',('"' + $InstallRoot + '"'))
$process = Start-Process -FilePath (Join-Path $controller 'runtime\python.exe') -ArgumentList $arguments -WorkingDirectory $InstallRoot -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput ($logBase + '.out.log') -RedirectStandardError ($logBase + '.err.log')
exit $process.ExitCode
