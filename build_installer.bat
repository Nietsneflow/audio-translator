@echo off
setlocal
cd /d "%~dp0"
title Build Installer Package

set "ROOT=%~dp0"
set "ZIP_FILE=%ROOT%Install\AudioTranslator.zip"

echo ================================================
echo   Building AudioTranslator installer package
echo ================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%build_installer.ps1"

if errorlevel 1 (
    echo.
    echo ERROR: Build failed.
    pause
    exit /b 1
)

pause