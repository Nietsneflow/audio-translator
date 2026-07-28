@echo off
title Audio Translator - Repair
cd /d "%~dp0"

echo =============================================
echo   Audio Translator - Repair
echo =============================================
echo.

:: Repair is intentionally destructive: it force-resets every app file to the
:: latest released version.  The checks below only make sure it resets the
:: RIGHT repository and fails loudly instead of pretending success offline.

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed.
    echo Run Update.bat first - it can install Git automatically.
    goto :done
)

git rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not connected to Git yet.
    echo Run Update.bat first - it sets up the connection automatically.
    goto :done
)

:: Confirm this folder itself is the repo root — rev-parse succeeds from any
:: subfolder of any repo, and resetting a parent repo would destroy it.
set "REPO_ROOT="
for /f "delims=" %%t in ('git rev-parse --show-toplevel 2^>nul') do set "REPO_ROOT=%%t"
if not defined REPO_ROOT (
    echo ERROR: Could not determine the Git repository root.
    goto :done
)
set "REPO_ROOT=%REPO_ROOT:/=\%"
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
if /i not "%REPO_ROOT%"=="%HERE%" (
    echo ERROR: This folder is inside a different Git repository:
    echo   %REPO_ROOT%
    echo Repair aborted so that repository is not reset.
    goto :done
)

echo Resetting app files to latest version...
echo.

git fetch origin main
if errorlevel 1 (
    echo.
    echo ERROR: Could not reach the update server.
    echo Check your internet connection and try again.
    goto :done
)

git reset --hard origin/main
if errorlevel 1 (
    echo.
    echo ERROR: Repair failed. Check the error above.
    goto :done
)

echo.
echo Repair complete. Running update now...
echo.

call "%~dp0Update.bat"

:done
echo.
pause
