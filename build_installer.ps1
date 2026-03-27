$root = $PSScriptRoot
$zipFile = Join-Path $root "Install\AudioTranslator.zip"

Write-Host "================================================"
Write-Host "  Building AudioTranslator installer package"
Write-Host "================================================"
Write-Host ""

# Remove old zip
if (Test-Path $zipFile) {
    Remove-Item $zipFile -Force
    Write-Host "Removed old package."
}

# Files to include
$files = @(
    "main.py",
    "gui.py",
    "transcriber.py",
    "audio_capture.py",
    "logger.py",
    "requirements.txt",
    "setup.bat",
    "Launch.bat",
    "readme.txt"
) | ForEach-Object { Join-Path $root $_ }

# Verify all source files exist
$missing = $files | Where-Object { -not (Test-Path $_) }
if ($missing) {
    Write-Host "ERROR: Missing files:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}

Write-Host "Packing files..."
Compress-Archive -Path $files -DestinationPath $zipFile -CompressionLevel Optimal

$size = [math]::Round((Get-Item $zipFile).Length / 1KB, 1)
Write-Host ""
Write-Host "Package created:" -ForegroundColor Green
Write-Host "  $zipFile"
Write-Host "  Size: $size KB"
Write-Host ""
Write-Host "Share Install\AudioTranslator.zip with your friend."
Write-Host "They just need to: Extract > double-click setup.bat"
