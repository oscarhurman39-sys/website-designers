# Registers the pipeline watchdog as a Windows Scheduled Task and starts it.
# Run once from the repo root in PowerShell:  .\install_autostart.ps1
# Remove with:  Unregister-ScheduledTask -TaskName "CaseyPipelineWatchdog" -Confirm:$false
#
# The task runs tools\watchdog.ps1 every 10 minutes on a TIMER trigger, not at
# logon. This PC sleeps rather than shuts down, so a logon trigger (what this
# script used to register) fires roughly never. watchdog.ps1 calls
# `run.py ops start`, which only launches what is missing, so a tick on a
# healthy machine is a no-op. Details: docs/STARNET_WRITE_SURFACE.md section 0.
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $repo 'tools\watchdog.ps1'
$name = 'CaseyPipelineWatchdog'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
try {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description 'Every 10 min: run.py ops start (idempotent). Timer trigger, not logon: this PC sleeps rather than shuts down.' -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Host "Could not register the scheduled task: $($_.Exception.Message)"
    Write-Host "Re-run from a PowerShell opened with 'Run as administrator':"
    Write-Host "    powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}
# The old logon-trigger task, if a previous version of this script registered it.
Unregister-ScheduledTask -TaskName 'website-designers' -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Registered '$name' (tools\watchdog.ps1 every 10 minutes). Running it once now..."
Start-ScheduledTask -TaskName $name
