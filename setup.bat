@echo off

:: ── Logging: re-run through PowerShell Tee so all output ──
:: ── goes to both the console AND install_log.txt           ──
if "%1"=="--logged" goto :main
powershell -NoProfile -ExecutionPolicy Bypass -Command ^windowed^
    "cmd /c '\"%~f0\" --logged' 2>&1 | Tee-Object -FilePath '%~dp0install_log.txt'"
exit /b %ERRORLEVEL%

:main
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

echo [ERROR] Python not found. Attempting to install Python 3.11 via winget...
echo (This requires Windows 10 version 1709 or later)
echo.
winget --version >nul 2>&1
if errorlevel 1 goto :no_winget

winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :winget_failed

:: Refresh PATH for this session by locating the new python.exe
for /f "tokens=*" %%p in ('where python 2^>nul') do (
    set "PYTHON_EXE=%%p"
    goto :python_found_path
)
:: If where fails, winget may have installed but PATH not yet refreshed
echo.
echo Python was installed but this window needs to be restarted to see it.
echo Please close this window and double-click setup.bat again.
pause
exit /b 0

:python_found_path
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

:: ── Step 2: Upgrade pip ───────────────────────────
echo [1/3] Upgrading pip...
python -m pip install --upgrade pip -q
echo       Done.
echo.

:: ── Step 3: Core dependencies ─────────────────────
echo [2/3] Installing core dependencies...
pip install "faster-whisper>=1.0.0" "soundcard>=0.4.3" "numpy>=1.24.0,<2.0" "psutil>=5.9.0" "pynvml>=11.0.0"
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
pip install "torch>=2.0.0" --index-url https://download.pytorch.org/whl/cu121
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
echo On first launch the Whisper speech recognition model
echo will download automatically (500 MB - 1.5 GB).
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
