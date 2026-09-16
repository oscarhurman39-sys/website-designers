@echo off
REM Starts the two processes that make PUBLIC_BASE_URL reachable:
REM   1. webhook_server.py on port 5000 (unsubscribe, click tracking, Stripe webhook, screenshots)
REM   2. ngrok tunnel on the free static dev domain set in .env's PUBLIC_BASE_URL
REM Each opens in its own window. Close the windows to stop them.
cd /d "%~dp0"
start "webhook_server" cmd /k "venv\Scripts\python.exe pipeline\webhook_server.py"
start "ngrok" cmd /k "ngrok http --url=tinsmith-persecute-freedom.ngrok-free.dev 5000"
echo Started webhook_server (port 5000) and ngrok tunnel. Both windows must stay open while the pipeline runs.
