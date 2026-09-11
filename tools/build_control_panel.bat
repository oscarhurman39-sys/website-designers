@echo off
REM Rebuilds the "Casey Websites" desktop app (tools\control_panel.py) into a single exe
REM and copies it to the Desktop. Run from anywhere.
cd /d "%~dp0.."
venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name "Casey Websites" --icon tools\casey.ico --distpath dist --workpath build --specpath build tools\control_panel.py
copy /Y "dist\Casey Websites.exe" "%USERPROFILE%\Desktop\Casey Websites.exe"
echo Built dist\Casey Websites.exe and copied it to the Desktop.
