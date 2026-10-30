[CmdletBinding()]
param([string]$Cache = (Join-Path (Split-Path $PSScriptRoot -Parent) '.local\build-cache'))
$ErrorActionPreference = 'Stop'
$Cache = [IO.Path]::GetFullPath($Cache)
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
function Fetch-Verified($Url, $Name, $Digest) {
    $destination = Join-Path $Cache $Name
    if (-not (Test-Path -LiteralPath $destination)) { Invoke-WebRequest -Uri $Url -OutFile $destination }
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLower() -ne $Digest) {
        throw "Checksum mismatch: $Name"
    }
    return $destination
}
$sourceArchive = Fetch-Verified 'https://www.python.org/ftp/python/3.12.16/Python-3.12.16.tgz' 'Python-3.12.16.tgz' '710c3e75dac8b2f258d9cd0f76e5c4c2e9175fac9e249c98901552f7cf33b7dd'
$crtArchive = Fetch-Verified 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' 'python-3.12.10-embed-amd64.zip' '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
$source = Join-Path $Cache 'Python-3.12.16'
if (-not (Test-Path -LiteralPath $source)) {
    & tar.exe -xzf $sourceArchive -C $Cache
    if ($LASTEXITCODE -ne 0) { throw 'Source extraction failed' }
}
$vswherePath = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsInstall = & $vswherePath -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsInstall) { throw 'The build machine needs MSVC x64 Build Tools' }
$previousTkinter = $env:IncludeTkinter
$env:IncludeTkinter = 'false'
try { & (Join-Path $source 'PCbuild\get_externals.bat') } finally { $env:IncludeTkinter = $previousTkinter }
if ($LASTEXITCODE -ne 0) { throw 'CPython external dependencies failed' }
$msbuild = Join-Path $vsInstall 'MSBuild\Current\Bin\MSBuild.exe'
& $msbuild (Join-Path $source 'PCbuild\pcbuild.proj') /t:Build /m /v:q /nologo /p:Configuration=Release /p:Platform=x64 /p:IncludeExternals=true /p:IncludeCTypes=true /p:IncludeSSL=true /p:IncludeTkinter=false /p:KillPython=false /p:IncludeTests=false
if ($LASTEXITCODE -ne 0) { throw 'CPython compilation failed' }
$runtime = Join-Path $Cache 'runtime-3.12.16'
$builtPython = Join-Path $source 'PCbuild\amd64\python.exe'
# Distribute only the VC runtime from the checksum-verified official CPython package.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($crtArchive)
try {
    foreach ($entry in $zip.Entries) {
        if ($entry.Name -match '^vcruntime140(_1)?\.dll$') {
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path (Split-Path $builtPython -Parent) $entry.Name), $true)
        }
    }
} finally { $zip.Dispose() }
& $builtPython (Join-Path $source 'PC\layout') --source $source --build (Join-Path $source 'PCbuild\amd64') --preset-embed --copy $runtime --arch amd64
if ($LASTEXITCODE -ne 0) { throw 'CPython runtime layout failed' }
& (Join-Path $runtime 'python.exe') -c 'import ssl,sqlite3,ctypes,zlib; print(ssl.OPENSSL_VERSION); print(sqlite3.sqlite_version)'
if ($LASTEXITCODE -ne 0) { throw 'Embedded runtime verification failed' }
Write-Output "Runtime ready: $runtime"
