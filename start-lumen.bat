@echo off
REM Lumen - local host. Windows: double-click this file.
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo Python 3.10+ is required. Install from python.org and tick "Add python.exe to PATH".
  pause & exit /b 1
)
echo Lumen - local-drive-first media collaboration
if not exist "data\catalogue.db" (
  echo   first run: creating a synthetic sample drive so you have something to click
  py -3 -m lfmc.cli seed-demo --dir data\sample_drive
  echo   adding sample comments, selects and a client review link
  py -3 -m lfmc.cli seed-reviews
)
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8765/"
echo   opening http://127.0.0.1:8765  ^(close this window to stop^)
py -3 -u -m lfmc.cli serve --host 127.0.0.1 --port 8765 --demo
pause
