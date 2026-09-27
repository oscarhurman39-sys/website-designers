# One-time fix: stop CaseyPipelineWatchdog flashing a PowerShell window every
# 10 minutes.
#
# The task asks PowerShell for a hidden window, but it runs as an INTERACTIVE
# logon task, so Windows creates the console on the desktop first and PowerShell
# hides it a fraction of a second later. You see the gap.
#
# This switches the task to S4U ("run whether user is logged on or not", with no
# stored password), which runs it in the background session where no console is
# ever created. Side effect Oscar accepted on 2026-09-20: when the watchdog
# restarts the pipeline, those processes start in the background session too, so
# they no longer appear under your own user in Task Manager. `run.py ops status`
# still finds them, because it checks process ids and ports, not sessions.
#
# Must be run elevated. Nothing else about the task changes: same 10-minute
# trigger, same script, same limited (non-admin) run level.
$ErrorActionPreference = 'Stop'
$name = 'CaseyPipelineWatchdog'

$t = Get-ScheduledTask -TaskName $name
Write-Host "BEFORE: LogonType=$($t.Principal.LogonType) RunLevel=$($t.Principal.RunLevel) Hidden=$($t.Settings.Hidden)"

$principal = New-ScheduledTaskPrincipal -UserId $t.Principal.UserId -LogonType S4U -RunLevel Limited
$settings = $t.Settings
$settings.Hidden = $true

try {
    Set-ScheduledTask -TaskName $name -Principal $principal -Settings $settings | Out-Null
} catch {
    Write-Host ""
    Write-Host "FAILED: $_" -ForegroundColor Red
    Write-Host "If this says the account lacks a required privilege, the fix is to grant"
    Write-Host "'Log on as a batch job' to your account in secpol.msc, or use the shim"
    Write-Host "approach instead. Do NOT switch the task to a stored password."
    Read-Host "Press Enter to close"
    exit 1
}

$n = Get-ScheduledTask -TaskName $name
Write-Host "AFTER:  LogonType=$($n.Principal.LogonType) RunLevel=$($n.Principal.RunLevel) Hidden=$($n.Settings.Hidden)"
Write-Host "Trigger unchanged: every $(($n.Triggers | ForEach-Object { $_.Repetition.Interval }) -join ',')"

Write-Host ""
Write-Host "Running it once now to prove it is silent and still works..."
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 12
$info = Get-ScheduledTaskInfo -TaskName $name
Write-Host "Last run: $($info.LastRunTime)   result: $($info.LastTaskResult)  (0 = success)"
Write-Host "Next run: $($info.NextRunTime)"
Write-Host ""
Write-Host "If no window flashed just now, it is fixed." -ForegroundColor Green
Read-Host "Press Enter to close"
