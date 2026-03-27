@echo off
cd /d "%~dp0"
set "LOGDIR=%~dp0logs"
set "LOGFILE=%LOGDIR%\launch_errors.txt"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

:: ── Check Python is available ────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] ERROR: Python not found in PATH. Run setup.bat. >> "%LOGFILE%"
    echo ERROR: Python is not installed or not in PATH.
    echo Please run setup.bat first.
    echo.
    echo Details logged to: %LOGFILE%
    pause
    exit /b 1
)

:: ── Check app files are present ──────────────────
if not exist "%~dp0main.py" (
    echo [%DATE% %TIME%] ERROR: main.py not found in %~dp0 >> "%LOGFILE%"
    echo ERROR: App files are missing.
    echo Make sure Launch.bat is in the same folder as main.py.
    echo.
    echo Details logged to: %LOGFILE%
    pause
    exit /b 1
)

:: ── Launch ───────────────────────────────────────
echo [%DATE% %TIME%] Launching Audio Translator... >> "%LOGFILE%"
start "" pythonw main.py

:: Wait 5 seconds then check if the process actually started
timeout /t 5 /nobreak >nul
tasklist /fi "imagename eq pythonw.exe" /fo csv 2>nul | find /i "pythonw.exe" >nul
if errorlevel 1 (
    echo [%DATE% %TIME%] WARNING: pythonw.exe not found 5s after launch. App may have crashed. >> "%LOGFILE%"
    echo WARNING: The app may have failed to start.
    echo Check logs\errors.log for details, or share that file for support.
    pause
)
