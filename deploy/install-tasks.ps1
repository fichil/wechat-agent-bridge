[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot, [Parameter(Mandatory=$true)][string]$UserName)
$ErrorActionPreference = 'Stop'
$InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'installation.json'))) { throw 'Bootstrap installation first' }
$principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserName
foreach ($entry in @(@('WeChatBridge-Run','run-installed.ps1'), @('WeChatBridge-Deploy','deploy-task.ps1'))) {
    $task = $entry[0]
    $script = Join-Path $InstallRoot $entry[1]
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $script + '" -InstallRoot "' + $InstallRoot + '"'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $InstallRoot
    Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings -Trigger $trigger -Force | Out-Null
}
Write-Output 'Interactive run and deployment tasks registered; no user password stored.'
