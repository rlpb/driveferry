@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://python.org and run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Setting up DriveFerry for the first time...
  py -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :fail
  ".venv\Scripts\python.exe" -m pip install --quiet pywebview || goto :fail
)

start "" ".venv\Scripts\pythonw.exe" -m driveferry %*
exit /b 0

:fail
echo Setup failed. Run this file from a command prompt to see the error.
pause
exit /b 1
