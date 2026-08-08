@echo off
rem Double-click launcher for the pipeline dashboard (Windows).
rem Put it on your desktop: right-click this file -> Send to ->
rem Desktop (create shortcut). Double-clicking starts the dashboard and
rem opens it in your browser; use its Start pipeline button from there.
cd /d "%~dp0"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"
where python >nul 2>nul
if errorlevel 1 (set "PY=py") else (set "PY=python")
%PY% -m streamlit run dashboard.py
echo.
echo Dashboard stopped. If it exited with an error, read the messages above.
pause
