[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$current = Get-Content -LiteralPath (Join-Path $InstallRoot 'current.json') -Raw | ConvertFrom-Json
if ($current.commit -notmatch '^[a-f0-9]{40}$') { throw 'Invalid installed commit' }
$permitPath = Join-Path $InstallRoot 'run-permit.json'
$permitted = $false
if (Test-Path -LiteralPath $permitPath) {
    $permit = Get-Content -LiteralPath $permitPath -Raw | ConvertFrom-Json
    $permitted = $permit.commit -eq $current.commit
    Remove-Item -LiteralPath $permitPath
}
if (-not $permitted) {
    if (Test-Path -LiteralPath (Join-Path $InstallRoot 'transaction.json')) { exit 0 }
    foreach ($statusFile in Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'status') -Filter '*.json' -ErrorAction SilentlyContinue) {
        $status = Get-Content -LiteralPath $statusFile.FullName -Raw | ConvertFrom-Json
        if ($status.stage -notin @('succeeded','failed','rolled_back','superseded')) { exit 0 }
    }
}
$release = Join-Path $InstallRoot ('releases\' + $current.commit)
$env:BRIDGE_DATA_DIR = Join-Path $InstallRoot 'data'
if ($current.data_path) { $env:BRIDGE_DATA_DIR = $current.data_path }
$config = Get-Content -LiteralPath (Join-Path $env:BRIDGE_DATA_DIR 'portable-config.json') -Raw | ConvertFrom-Json
$env:BRIDGE_ENV = $config.mode
$env:BRIDGE_PUBLIC_ORIGIN = $config.origin
$env:BRIDGE_AUTH_DATABASE = Join-Path $env:BRIDGE_DATA_DIR 'auth.sqlite3'
$env:BRIDGE_CADDY = Join-Path $release 'bin\caddy.exe'
Remove-Item Env:BRIDGE_DISABLE_MONITOR -ErrorAction SilentlyContinue
Set-Location -LiteralPath $release
# Keep the task alive with the supervisor. No SSH connection owns this tree.
& (Join-Path $release 'runtime\python.exe') -B -X utf8 (Join-Path $release 'scripts\production_server.py')
exit $LASTEXITCODE
