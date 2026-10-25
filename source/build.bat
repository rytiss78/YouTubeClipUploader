@echo off
rem Builds ..\YouTubeClipUploader.exe. Needs Python 3.10+ with the py launcher.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3 -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller==6.22.3 || goto :error
.venv\Scripts\python.exe -m unittest -q || goto :error
.venv\Scripts\python.exe build.py || goto :error
echo.
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
