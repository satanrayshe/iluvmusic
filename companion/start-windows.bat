@echo off
setlocal
title iluvmusic companion
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  iluvmusic needs Python 3.9 or newer.
  echo  Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
  echo  or run:  winget install Python.Python.3.12
  echo  then double-click this file again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  First run: setting things up. This takes a minute.
  echo.
  python -m venv .venv || goto :failed
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :failed
  echo.
  choice /c YN /m " Start iluvmusic automatically when you sign in to Windows"
  if not errorlevel 2 ".venv\Scripts\python.exe" iluvmusic_companion.py --install-autostart
  echo.
)

".venv\Scripts\python.exe" iluvmusic_companion.py %*
if errorlevel 1 pause
exit /b

:failed
echo.
echo  Setup failed. Check your internet connection and try again.
echo  If it keeps failing, delete the .venv folder next to this file and retry.
pause
exit /b 1
