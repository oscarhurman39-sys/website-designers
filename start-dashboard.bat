@echo off
setlocal
rem Double-click launcher for the pipeline dashboard (Windows).
rem
rem Does the whole setup itself, and is safe to run repeatedly -- every step
rem is skipped if it's already done, so after the first run this is just a
rem fast launch. Put it on your desktop: right-click -> Send to -> Desktop
rem (create shortcut), then double-click that icon whenever you want the
rem dashboard.
cd /d "%~dp0"
set "VENV_PY=venv\Scripts\python.exe"

rem --- Find a usable Python -----------------------------------------------
rem `py` (the launcher) is preferred: a bare `python` on Windows is often the
rem Microsoft Store stub, which exits without creating anything.
set "BOOT_PY="
py -3 --version >nul 2>nul && set "BOOT_PY=py -3"
if not defined BOOT_PY (python --version >nul 2>nul && set "BOOT_PY=python")
if not defined BOOT_PY (
    echo.
    echo   Python isn't installed, or isn't on your PATH.
    echo   Install it from https://www.python.org/downloads/ and tick
    echo   "Add python.exe to PATH" in the installer, then run this again.
    echo.
    pause
    exit /b 1
)

rem --- Create the virtual environment (first run only) --------------------
if not exist "%VENV_PY%" (
    echo [setup] Creating the virtual environment...
    %BOOT_PY% -m venv venv
    if not exist "%VENV_PY%" (
        echo.
        echo   Could not create the virtual environment. The output above says why.
        echo.
        pause
        exit /b 1
    )
)

rem --- Install dependencies (only when something is missing) --------------
rem streamlit is the canary: if it imports, a previous run already completed
rem the install, so skip it and launch straight away.
"%VENV_PY%" -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo [setup] Installing dependencies -- this takes a few minutes the first time...
    "%VENV_PY%" -m pip install --quiet --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   Dependency install failed. The output above says why -- a common
        echo   cause is a full disk; a few GB need to be free.
        echo.
        pause
        exit /b 1
    )
    echo [setup] Downloading the browser used for preview screenshots...
    "%VENV_PY%" -m playwright install chromium
)

rem --- Create .env from the template (first run only) ---------------------
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [setup] Created .env from the template -- it needs your real keys.
)

rem --- Check the config, and reopen .env until it's valid -----------------
:checkconfig
"%VENV_PY%" pipeline\config.py
if errorlevel 1 (
    echo.
    echo   Fill in the missing values listed above, then save and close Notepad.
    echo   Close Notepad without changes to give up and exit.
    echo.
    start /wait notepad .env
    "%VENV_PY%" pipeline\config.py >nul 2>nul
    if errorlevel 1 (
        echo.
        echo   Still missing values -- nothing was launched. Run this again when
        echo   you have the keys to hand.
        echo.
        pause
        exit /b 1
    )
)

rem --- Launch --------------------------------------------------------------
echo.
echo [ok] Starting the dashboard. Your browser should open automatically.
echo      Leave this window open -- closing it stops the dashboard.
echo.
"%VENV_PY%" -m streamlit run dashboard.py
echo.
echo Dashboard stopped. If that was unexpected, the messages above say why.
pause
