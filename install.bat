@echo off
cd /d "%~dp0"
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt

echo.
echo Installed.
echo.
echo If upgrading from a version that used a different YouTube scope,
echo delete token.json and run first_run.bat again.
echo.
pause
