@echo off

:: ── Logging: re-run through PowerShell Tee so all output ──
:: ── goes to both the console AND install_log.txt           ──
if "%1"=="--logged" goto :main
powershell -NoProfile -ExecutionPolicy Bypass -Command "cmd /c '\"%~f0\" --logged' 2>&1 | Tee-Object -FilePath '%~dp0install_log.txt'"
exit /b %ERRORLEVEL%

:main
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Audio Translator - Installer

echo ================================================
echo   Audio Translator Installer
echo ================================================
echo Started: %DATE% %TIME%
echo Log file: %~dp0install_log.txt
echo.

:: ── Step 1: Ensure Python is available ───────────
python --version >nul 2>&1
if not errorlevel 1 goto :python_ok

echo Python not found. Installing Python 3.11 automatically...
echo This may take a minute, please wait.
echo.
winget --version >nul 2>&1
if errorlevel 1 goto :no_winget

winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :winget_failed
echo Python 3.11 installed successfully.

:: winget installs Python but doesn't refresh the current cmd session's PATH.
:: The Windows Store app alias also shadows 'python' in a fresh session.
:: Look for the real python.exe directly in the known install locations.
set "PYTHON_EXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :python_found_path
)
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_EXE=%ProgramFiles%\Python311\python.exe"
    goto :python_found_path
)
echo.
echo Python was installed but could not be located automatically.
echo Please close this window and double-click setup.bat again.
pause
exit /b 0

:python_found_path
:: Add the found Python to PATH for the rest of this session
for %%d in ("%PYTHON_EXE%") do set "PATH=%%~dpd;%%~dpd\Scripts;%PATH%"
set "PYTHON_EXE="
goto :python_ok

:no_winget
:winget_failed
echo.
echo [ERROR] Could not auto-install Python.
echo Please download and install Python 3.11 from:
echo   https://www.python.org/downloads/
echo.
echo IMPORTANT: Check "Add Python to PATH" during installation.
echo Then double-click setup.bat again.
echo.
echo If you need help, share install_log.txt with the person who gave you this app.
start https://www.python.org/downloads/
pause
exit /b 1

:python_ok
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Found: %%v
echo.

:: ── Step 1b: Ensure Git is available (needed for Update.bat) ─────────────
git --version >nul 2>&1
if not errorlevel 1 goto :git_ok

echo Git not found. Installing Git automatically...
winget --version >nul 2>&1
if errorlevel 1 goto :git_no_winget

winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :git_no_winget

:: Refresh PATH so git is visible for the rest of this session
for /f "tokens=*" %%p in ('where git 2^>nul') do set "PATH=%PATH%;%%~dpp"
git --version >nul 2>&1
if not errorlevel 1 (
    echo Git installed successfully.
    goto :git_ok
)
echo Git was installed but needs a restart to take effect.
echo Close this window, open a new one, and re-run setup.bat to continue.
pause
exit /b 0

:git_no_winget
echo.
echo Could not auto-install Git.
echo Please download and install Git from:
echo   https://git-scm.com/download/win
echo Use all default options during install.
echo Then double-click setup.bat again.
echo.
start https://git-scm.com/download/win
pause
exit /b 1

:git_ok
for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo Found: %%v
echo.

:: ── Step 2: Upgrade pip ───────────────────────────
echo [1/3] Upgrading pip...
python -m pip install --upgrade pip -q
echo       Done.
echo.

:: ── Step 3: Core dependencies ─────────────────────
echo [2/3] Installing core dependencies...
python -m pip install "faster-whisper>=1.0.0" "soundcard>=0.4.3" "numpy>=1.24.0,<2.0" "psutil>=5.9.0" "pynvml>=11.0.0"
if errorlevel 1 (
    echo.
    echo [ERROR] Core dependency install failed.
    echo Share install_log.txt with the person who gave you this app for help.
    pause
    exit /b 1
)
echo       Done.
echo.

:: ── Step 4: PyTorch (large download) ─────────────
echo [3/3] Installing PyTorch with CUDA support...
echo       This downloads ~2.5 GB and may take several minutes.
echo       If you have no NVIDIA GPU this still works - it will use CPU.
echo.
python -m pip install "torch>=2.0.0" --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 (
    echo.
    echo [ERROR] PyTorch install failed.
    echo This may be a network issue or disk space issue (~3 GB required).
    echo Share install_log.txt with the person who gave you this app for help.
    pause
    exit /b 1
)
echo.

:: ── Done ──────────────────────────────────────────
echo ================================================
echo   Installation complete!
echo ================================================
echo.
echo Speech recognition models are included in the models\ folder.
echo No internet download of models is required.
echo.
echo Installation finished: %DATE% %TIME%
echo.
set /p "LAUNCH=Launch the app now? (Y/N): "
if /i "!LAUNCH!"=="Y" (
    start "" pythonw main.py
    echo Starting...
    timeout /t 3 /nobreak >nul
) else (
    echo To start later, double-click Launch.bat
)
echo.
echo If you have any problems, share install_log.txt for support.
pause
