[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot, [switch]$Background, [switch]$Wait)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
# Login and deployment may race. Do not open an obsolete version during a switch.
while (Test-Path -LiteralPath (Join-Path $InstallRoot 'transaction.json')) {
    if (-not $Background) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show('Application update in progress. Please reopen the console when it finishes.', 'WeChat Agent Bridge') | Out-Null
        exit 0
    }
    Start-Sleep -Seconds 1
}
$current = Get-Content -LiteralPath (Join-Path $InstallRoot 'current.json') -Raw | ConvertFrom-Json
if ($current.commit -notmatch '^[a-f0-9]{40}$') { throw 'Invalid installed commit' }
$directory = $current.commit
if ($current.directory) {
    if ($current.directory -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?-[a-f0-9]{40}$' -or -not $current.directory.EndsWith($current.commit)) { throw 'Invalid release directory' }
    $directory = $current.directory
}
$release = Join-Path $InstallRoot ('releases\' + $directory)
$manifest = Get-Content -LiteralPath (Join-Path $release 'portable-manifest.json') -Raw | ConvertFrom-Json
$launch = @{FilePath=(Join-Path $release 'WeChatAgentBridge.exe'); WorkingDirectory=$release; PassThru=$true}
if ($Background -and $manifest.launcher_protocol -ge 1) {
    $launch.ArgumentList = '--background'
    $launch.WindowStyle = 'Hidden'
} else { $launch.WindowStyle = 'Normal' }
# Wait only for this GUI process, not management children or the business service.
$process = Start-Process @launch
if ($Wait) { $process.WaitForExit(); exit $process.ExitCode }
