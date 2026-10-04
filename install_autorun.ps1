$ErrorActionPreference = 'Stop'
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $uploaderPython = $venvPython
} else {
    $uploaderPython = (& py -3 -c 'import sys; print(sys.executable)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python 3 is unavailable.' }
}
$windowlessPython = Join-Path (Split-Path $uploaderPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $windowlessPython)) { throw 'pythonw.exe is unavailable.' }
$scriptPath = Join-Path $PSScriptRoot 'clip_uploader.py'
$arguments = '"' + $scriptPath + '" --non-interactive'
$startupFolder = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupFolder 'YouTube Clip Uploader.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = $windowlessPython
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Prepare and upload new gameplay clips once at sign-in, then exit.'
$shortcut.Save()
Write-Output "Installed user startup launcher: $shortcutPath"
Write-Output 'At sign-in: scan once, automatically prepare/upload new clips, then exit.'
