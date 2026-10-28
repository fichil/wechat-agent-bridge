# Offscreen synthetic-image format control only. No windows, input, network or
# credentials. The caller creates the fixed 512x256 red/blue PNG exclusively.
param([Parameter(Mandatory=$true)][string]$SourcePng,
      [Parameter(Mandatory=$true)][string]$DestinationJpeg)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$m1Source=[IO.Path]::GetFullPath($SourcePng)
$m1Destination=[IO.Path]::GetFullPath($DestinationJpeg)
$m1Allowed=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.local/live-acceptance'))
if ([IO.Path]::GetDirectoryName($m1Source) -ne $m1Allowed -or
    [IO.Path]::GetDirectoryName($m1Destination) -ne $m1Allowed -or
    [IO.Path]::GetExtension($m1Source) -ne '.png' -or
    [IO.Path]::GetExtension($m1Destination) -ne '.jpg' -or
    [IO.File]::Exists($m1Destination)) {throw 'synthetic_jpeg_path_invalid'}
$m1Bitmap=$null;$m1Decoded=$null;$m1Output=$null
try {
    $m1Bitmap=[Drawing.Bitmap]::FromFile($m1Source)
    if ($m1Bitmap.Width -ne 512 -or $m1Bitmap.Height -ne 256) {throw 'synthetic_png_dimensions_invalid'}
    # Verify every source pixel, independently of the standard-library encoder.
    for ($m1Y=0;$m1Y -lt 256;$m1Y++) {
        for ($m1X=0;$m1X -lt 512;$m1X++) {
            $m1Color=$m1Bitmap.GetPixel($m1X,$m1Y)
            if (($m1X -lt 256 -and ($m1Color.R -ne 255 -or $m1Color.G -ne 0 -or $m1Color.B -ne 0)) -or
                ($m1X -ge 256 -and ($m1Color.R -ne 0 -or $m1Color.G -ne 0 -or $m1Color.B -ne 255))) {throw 'synthetic_png_pixel_mismatch'}
        }
    }
    $m1Output=[IO.File]::Open($m1Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    $m1Bitmap.Save($m1Output,[Drawing.Imaging.ImageFormat]::Jpeg)
    $m1Output.Dispose();$m1Output=$null
    $m1Decoded=[Drawing.Bitmap]::FromFile($m1Destination)
    if ($m1Decoded.Width -ne 512 -or $m1Decoded.Height -ne 256) {throw 'synthetic_jpeg_dimensions_invalid'}
    # JPEG is lossy; inspect a grid away from the high-contrast panel boundary.
    $m1Samples=0
    foreach ($m1Y in @(16,64,128,192,240)) {
        foreach ($m1X in @(16,64,128,192,240,272,320,384,448,496)) {
            $m1Color=$m1Decoded.GetPixel($m1X,$m1Y)
            if (($m1X -lt 256 -and ($m1Color.R -lt 250 -or $m1Color.G -gt 5 -or $m1Color.B -gt 5)) -or
                ($m1X -gt 256 -and ($m1Color.R -gt 5 -or $m1Color.G -gt 5 -or $m1Color.B -lt 250))) {throw 'synthetic_jpeg_pixel_mismatch'}
            $m1Samples++
        }
    }
    @{source_pixels_verified=131072;jpeg_samples_verified=$m1Samples;width=512;height=256;
      conversion='Windows System.Drawing offscreen PNG to JPEG';
      drawing_version=[Drawing.Bitmap].Assembly.GetName().Version.ToString()} | ConvertTo-Json -Compress
}
finally {
    if ($null -ne $m1Output) {$m1Output.Dispose()}
    if ($null -ne $m1Decoded) {$m1Decoded.Dispose()}
    if ($null -ne $m1Bitmap) {$m1Bitmap.Dispose()}
}
