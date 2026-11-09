[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot, [Parameter(Mandatory=$true)][string]$UserName, [switch]$ConsoleOnly)
$ErrorActionPreference = 'Stop'
$InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'installation.json'))) { throw 'Bootstrap installation first' }
$principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserName
$entries = ,@('WeChatBridge-Console','open-installed.ps1')
if (-not $ConsoleOnly) { $entries += @(@('WeChatBridge-Run','run-installed.ps1'), @('WeChatBridge-Deploy','deploy-task.ps1')) }
foreach ($entry in $entries) {
    $task = $entry[0]
    $script = Join-Path $InstallRoot $entry[1]
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $script + '" -InstallRoot "' + $InstallRoot + '"'
    if ($task -eq 'WeChatBridge-Console') { $arguments += ' -Background -Wait' }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $InstallRoot
    Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings -Trigger $trigger -Force | Out-Null
}
# Use the interactive WeChat user's desktop, never the public desktop.
$desktop = [Environment]::GetFolderPath('Desktop')
if ($desktop) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $desktop 'WeChat Agent Bridge.lnk'))
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $shortcut.Arguments = '-NoProfile -WindowStyle Hidden -File "' + (Join-Path $InstallRoot 'open-installed.ps1') + '" -InstallRoot "' + $InstallRoot + '"'
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.Description = 'WeChat Agent Bridge console and password recovery'
    $shortcut.Save()
}
Write-Output 'Interactive console task registered; no user password stored.'
