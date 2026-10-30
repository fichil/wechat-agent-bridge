$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$buildPath = Join-Path $projectRoot '.local\native-send'
New-Item -ItemType Directory -Path $buildPath -Force | Out-Null
$vswherePath = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsInstall = & $vswherePath -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsInstall) { throw 'MSVC x64 Build Tools are required.' }
$vcvarsPath = Join-Path $vsInstall 'VC\Auxiliary\Build\vcvars64.bat'
Push-Location $buildPath
try {
    $compileCommand = 'call "' + $vcvarsPath + '" >nul && cl.exe /nologo /std:c++17 /EHsc /W4 /utf-8 /LD /MD /O2 /DNDEBUG /Brepro "..\..\native_send\text_bridge.cpp" /Fe:bridge_text.dll /Fo:bridge_text.obj /link /MACHINE:X64 /INCREMENTAL:NO bcrypt.lib'
    & $env:ComSpec /d /s /c $compileCommand
    if ($LASTEXITCODE -ne 0) { throw 'Native text bridge compilation failed.' }
    $manifest = @{
        dll_sha256 = (Get-FileHash -LiteralPath 'bridge_text.dll' -Algorithm SHA256).Hash.ToLower()
        sources = @{}
    }
    foreach ($file in @('text_bridge.cpp','core_version.h')) {
        $manifest.sources[$file] = (Get-FileHash -LiteralPath (Join-Path $projectRoot "native_send\$file") -Algorithm SHA256).Hash.ToLower()
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath 'build.json' -Encoding utf8
} finally { Pop-Location }
