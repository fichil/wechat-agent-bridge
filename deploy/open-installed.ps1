[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$current = Get-Content -LiteralPath (Join-Path $InstallRoot 'current.json') -Raw | ConvertFrom-Json
if ($current.commit -notmatch '^[a-f0-9]{40}$') { throw 'Invalid installed commit' }
Start-Process -FilePath (Join-Path $InstallRoot ('releases\' + $current.commit + '\WeChatAgentBridge.exe')) -WindowStyle Hidden
