@echo off
schtasks /Delete /TN "YouTube Clip Uploader" /F
powershell.exe -NoProfile -Command "Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Startup')) 'YouTube Clip Uploader.lnk') -ErrorAction SilentlyContinue"
pause
