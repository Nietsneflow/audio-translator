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

:: Pull latest changes
echo Checking for updates...
echo.
git pull
if errorlevel 1 (
    echo.
    echo Update failed. Check the error above.
    echo If you see "not a git repository", the folder was not cloned via Git.
    echo Ask for a fresh download link or re-clone the repository.
    goto :done
)

:: Update Python dependencies in case requirements.txt changed
echo.
echo Updating dependencies...
echo.
call ".venv\Scripts\activate.bat" 2>nul
if errorlevel 1 (
    echo WARNING: Virtual environment not found at .venv\
    echo Skipping dependency update.
    goto :done
)
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
