@echo off
cd /d "%~dp0"

schtasks /Delete /TN "YouTube Clip Uploader" /F >nul 2>&1
schtasks /Create /TN "YouTube Clip Uploader" /SC ONLOGON /TR "wscript.exe \"%~dp0run_silent.vbs\"" /RL LIMITED /F

echo.
echo Startup task installed.
echo At each Windows logon it will scan once, upload any clips found,
echo then exit. It will NOT remain running in the background.
echo.
pause
