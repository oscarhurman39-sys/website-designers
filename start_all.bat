@echo off
rem Idempotent: safe to run at logon, from the scheduled task, or by hand while
rem some or all of the pipeline is already up -- nothing gets started twice.
cd /d "%~dp0"

rem Webhook server + ngrok: run.py serve-public checks what is already up,
rem starts only what is missing (each in its own console) and waits for the
rem public /health to answer.
venv\Scripts\python.exe run.py serve-public

rem Pipeline supervisor: only if no scheduler.py is already running.
powershell -NoProfile -Command "exit [int]((Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'scheduler\.py' }).Count -gt 0)"
if %errorlevel% equ 0 (
  start "pipeline" cmd /k "cd pipeline && ..\venv\Scripts\python.exe scheduler.py"
  echo Started the pipeline supervisor.
) else (
  echo Pipeline supervisor already running; not starting a second one.
)
