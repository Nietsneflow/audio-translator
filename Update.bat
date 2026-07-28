@echo off
:: ── Re-run through PowerShell Tee so all output goes to both ──
:: ── the console AND install_log.txt                           ──
if "%1"=="--logged" goto :main
powershell -NoProfile -ExecutionPolicy Bypass -Command "cmd /c '\"%~f0\" --logged' 2>&1 | Tee-Object -FilePath '%~dp0install_log.txt'"
exit /b %ERRORLEVEL%

:main
title Audio Translator — Update
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo =============================================
echo   Audio Translator — Update
echo =============================================
echo Started: %DATE% %TIME%
echo Log file: %~dp0install_log.txt
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

:: Check this folder is a git repository — if not, initialize it and connect to GitHub
git rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
    echo This folder is not connected to Git. Setting it up now...
    echo.
    git init
    git remote add origin https://github.com/Nietsneflow/audio-translator.git
    git fetch origin main
    if errorlevel 1 (
        echo.
        echo ERROR: Could not connect to the update server.
        echo Please check your internet connection and try again.
        goto :done
    )
    git reset --hard origin/main
    if errorlevel 1 (
        echo.
        echo ERROR: Could not apply the downloaded update.
        goto :done
    )
    echo.
    echo Folder connected to Git successfully.
    echo.
    goto :post_pull
)

:: Safety: confirm this folder itself is the repo root.  rev-parse succeeds
:: from any subfolder of any repo, so without this check, extracting the app
:: inside another Git repository would hard-reset THAT repository.
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
    echo Update aborted so that repository is not reset.
    echo Move the app to its own folder and run Update.bat again.
    goto :done
)

:: Safety: reset --hard permanently discards local edits — ask first.
:: Untracked files are excluded: reset --hard never touches them, so they
:: must not trigger the scary prompt.  (No pipe to find.exe here on purpose:
:: with Git Bash/MSYS tools on PATH, "find" can resolve to GNU find and hang.)
set "DIRTY="
for /f "delims=" %%c in ('git status --porcelain --untracked-files^=no') do set "DIRTY=1"
if defined DIRTY (
    echo WARNING: You have local changes to these app files:
    git status --short --untracked-files=no
    echo.
    echo Updating will PERMANENTLY DISCARD the changes above.
    echo Continue and discard local changes? Type Y then Enter to continue:
    set /p "CONT="
    if /i not "!CONT!"=="Y" (
        echo Update cancelled - no files were changed.
        goto :done
    )
)

:: Pull latest changes
echo Checking for updates...
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
    echo Update failed. Check the error above.
    goto :done
)

:post_pull

:: Update Python dependencies in case requirements.txt changed
echo.
echo Updating dependencies...
echo.
:: python -m pip guarantees the same interpreter Launch.bat's pythonw uses;
:: bare "pip" can resolve to a different Python on multi-install machines.
python -m pip install -r requirements.txt -q
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
