@echo off
cd /d "%~dp0"

:: Find python — prefer explicit path from venv, fall back to launcher
set PYTHON_CMD=py -3
if exist "%~dp0.venv\Scripts\python.exe" set PYTHON_CMD="%~dp0.venv\Scripts\python.exe"

:: Verify we can actually run it
%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not available. Install Python 3.x or create a venv first.
    pause
    exit /b 1
)

:: Install a silent one-shot sign-in launcher with a resolved Python executable.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autorun.ps1"
if errorlevel 1 (
    echo ERROR: Could not install the startup task.
    pause
    exit /b 1
)

echo.
echo Startup launcher installed.
echo At sign-in it prepares and uploads new clips once, then exits.
echo.
pause
