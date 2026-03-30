@echo off
title Audio Translator — Update
cd /d "%~dp0"

echo =============================================
echo   Audio Translator — Check for Updates
echo =============================================
echo.

:: Check git is installed — auto-install via winget if missing
where git >nul 2>&1
if errorlevel 1 (
    echo Git is not installed. Attempting automatic installation...
    echo.
    winget --version >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Could not auto-install Git ^(winget not available^).
        echo.
        echo Please download and install Git manually from:
        echo   https://git-scm.com/download/win
        echo.
        echo After installing, close this window and run Update.bat again.
        goto :done
    )
    winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo ERROR: Git installation failed.
        echo.
        echo Please download and install Git manually from:
        echo   https://git-scm.com/download/win
        echo.
        echo After installing, close this window and run Update.bat again.
        goto :done
    )
    :: Refresh PATH so git is available in this session
    for /f "tokens=*" %%i in ('where /r "%ProgramFiles%\Git" git.exe 2^>nul') do set "GIT_EXE=%%i"
    if defined GIT_EXE (
        for %%d in ("%GIT_EXE%") do set "PATH=%%~dpd;%PATH%"
    )
    echo Git installed successfully.
    echo.
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

:: Download / verify the English→Russian translation pack
echo.
echo Setting up translation (English to Russian)...
echo This may download ~80 MB the first time. Please wait...
echo.
python download_translation_model.py
if errorlevel 1 (
    echo WARNING: Translation setup had errors. The app will still work,
    echo but the Translate English to Russian feature may not be available.
) else (
    echo Translation is ready.
)

echo.
echo =============================================
echo   Update complete! You can close this window.
echo =============================================

:done
echo.
pause
