@echo off
rem Create the virtual environment and install dependencies.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 (
    echo [SnapTrans] Failed to create venv. Is Python 3.10+ installed and on PATH?
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [SnapTrans] Failed to install dependencies, see the errors above.
  pause
  exit /b 1
)
echo.
echo [SnapTrans] Done. Double-click start.bat to run.
pause
