$ErrorActionPreference = 'Stop'
$taskName = 'YouTube Clip Uploader'
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $uploaderPython = $venvPython
} else {
    $uploaderPython = (& py -3 -c 'import sys; print(sys.executable)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python 3 is unavailable.' }
}
$windowlessPython = Join-Path (Split-Path $uploaderPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $windowlessPython)) { throw 'pythonw.exe is unavailable.' }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'startup_task_backup.xml') -Encoding Unicode
}
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $windowlessPython -Argument ('"' + (Join-Path $PSScriptRoot 'clip_uploader.py') + '" --non-interactive') -WorkingDirectory $PSScriptRoot
$logon = New-ScheduledTaskTrigger -AtLogOn -User $account
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $logon -Principal $principal -Settings $settings -Description 'Prepare and upload gameplay clips once at sign-in, then exit.' -Force | Out-Null
Write-Output "Installed '$taskName': prepare and upload once at sign-in, then exit."
