@echo off
if exist "%~dp0WeChatAgentBridge.exe" (
    start "" "%~dp0WeChatAgentBridge.exe"
) else (
    powershell.exe -NoProfile -WindowStyle Hidden -File "%~dp0open-installed.ps1" -InstallRoot "%~dp0"
)
