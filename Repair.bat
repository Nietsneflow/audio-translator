@echo off
title Audio Translator - Repair
cd /d "%~dp0"

echo =============================================
echo   Audio Translator - Repair
echo =============================================
echo.
echo Resetting app files to latest version...
echo.

git fetch origin main
git reset --hard origin/main

if errorlevel 1 (
    echo.
    echo ERROR: Repair failed. Check your internet connection and try again.
    goto :done
)

echo.
echo Repair complete. Running update now...
echo.

call "%~dp0Update.bat"

:done
echo.
pause
