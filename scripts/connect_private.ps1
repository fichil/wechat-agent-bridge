[CmdletBinding()]
param([string]$Server, [string]$UserName, [string]$IdentityFile, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$sshCommand = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $sshCommand) { throw 'Windows OpenSSH Client is required on this computer.' }
if (-not $Server) { $Server = Read-Host 'Server IP or configured SSH alias' }
if ($Server -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$') { throw 'Invalid server or SSH alias.' }
if (-not $UserName) { $UserName = Read-Host 'SSH user (Enter to use SSH config/default)' }
if ($UserName -and $UserName -notmatch '^[A-Za-z0-9_.-]{1,128}$') { throw 'Invalid SSH user.' }
if (Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Local port 18765 is occupied. Close your previous Bridge tunnel first; no process was stopped.'
}
$destination = if ($UserName) { $UserName + '@' + $Server } else { $Server }
$sshArguments = @('-N','-T','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30',
                  '-o','ServerAliveCountMax=3','-L','127.0.0.1:18765:127.0.0.1:18765')
if ($IdentityFile) {
    $keyPath = (Resolve-Path -LiteralPath $IdentityFile).Path
    if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf) -or $keyPath.Contains('"')) { throw 'Invalid identity file.' }
    $sshArguments += @('-i', ('"' + $keyPath + '"'), '-o', 'IdentitiesOnly=yes')
}
$sshArguments += $destination
# SSH uses this interactive console for host-key confirmation or credentials.
# Passwords are neither read by this script nor written to arguments/config/logs.
$sshProcess = Start-Process -FilePath $sshCommand.Source -ArgumentList $sshArguments -NoNewWindow -PassThru
try {
    $deadline = (Get-Date).AddMinutes(2)
    do {
        if ($sshProcess.HasExited) { throw 'SSH disconnected. Check the connection message above.' }
        $listener = Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -eq $sshProcess.Id -and $_.LocalAddress -eq '127.0.0.1' }
        if ($listener) { break }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    if (-not $listener) { throw 'SSH tunnel did not become ready.' }
    Write-Host 'Connected: http://127.0.0.1:18765 . Keep this console open; Ctrl+C disconnects.'
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:18765' }
    $sshProcess.WaitForExit()
} finally {
    if (-not $sshProcess.HasExited) { $sshProcess.Kill() }
    $sshProcess.Dispose()
}
