# Registers a Windows Scheduled Task that runs start_all.bat every time you log
# on, so the pipeline, webhook server and ngrok come back after a reboot.
# Run once from the repo root in PowerShell:  .\install_autostart.ps1
# Remove with:  Unregister-ScheduledTask -TaskName "website-designers" -Confirm:$false
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$repo\start_all.bat`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
try {
    Register-ScheduledTask -TaskName "website-designers" -Action $action -Trigger $trigger -Settings $settings -Description "Starts the website-designers pipeline, webhook server and ngrok at logon" -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Host "Could not register the scheduled task: $($_.Exception.Message)"
    Write-Host "Task Scheduler refused this shell. Re-run from a PowerShell opened with 'Run as administrator':"
    Write-Host "    powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}
Write-Host "Registered scheduled task 'website-designers' (runs start_all.bat at logon). Starting it now (start_all.bat is idempotent, so anything already running is left alone)..."
Start-ScheduledTask -TaskName "website-designers"
