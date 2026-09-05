@echo off
REM Windows "always-on while this PC is up": starts everything the pipeline
REM needs in three windows. install_autostart.ps1 registers this to run at logon.
REM   1. webhook_server.py on :5000 (unsubscribe, click tracking, Stripe webhook, screenshots)
REM   2. ngrok tunnel on the static dev domain in .env's PUBLIC_BASE_URL
REM   3. scheduler.py supervisor -> restarts main.py (the sales loop) if it crashes
cd /d "%~dp0"
start "webhook_server" /min cmd /k "venv\Scripts\python.exe pipeline\webhook_server.py"
start "ngrok" /min cmd /k "ngrok http --url=tinsmith-persecute-freedom.ngrok-free.dev 5000"
timeout /t 3 /nobreak >nul
start "pipeline" cmd /k "cd pipeline && ..\venv\Scripts\python.exe scheduler.py"
echo Started webhook_server, ngrok and the pipeline supervisor. Close the windows to stop.
