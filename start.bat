@echo off
rem Start SnapTrans without a console window. Run setup.bat first.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo [SnapTrans] .venv not found. Please run setup.bat first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0run.pyw"
