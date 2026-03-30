@echo off
title Audio Translator — Update
cd /d "%~dp0"

echo =============================================
echo   Audio Translator — Check for Updates
echo =============================================
echo.

:: Check git is installed
where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed or not in PATH.
    echo.
    echo Please download and install Git from:
    echo   https://git-scm.com/download/win
    echo.
    echo After installing, close this window and run Update.bat again.
    goto :done
)

:: Check this folder is a git repository
if not exist ".git" (
    echo ERROR: This folder was not set up via Git.
    echo.
    echo Update.bat only works if the app was installed by cloning the repository.
    echo Please ask for a fresh install link or re-run setup.bat.
    goto :done
)

:: Pull latest changes
echo Checking for updates...
echo.
git pull
if errorlevel 1 (
    echo.
    echo Update failed. Check the error above.
    goto :done
)

:: Update Python dependencies in case requirements.txt changed
echo.
echo Updating dependencies...
echo.
pip install -r requirements.txt -q
if errorlevel 1 (
    echo WARNING: Dependency update had errors. The app may still work.
) else (
    echo Dependencies are up to date.
)

echo.
echo =============================================
echo   Update complete! You can close this window.
echo =============================================

:done
echo.
pause
