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

schtasks /Delete /TN "YouTube Clip Uploader" /F >nul 2>&1
schtasks /Create /TN "YouTube Clip Uploader" /SC ONLOGON /TR "%PYTHON_CMD% clip_uploader.py" /RL LIMITED /F

echo.
echo Startup task installed.
echo At each Windows logon it will scan once, upload any clips found,
echo then exit. It will NOT remain running in the background.
echo.
pause
