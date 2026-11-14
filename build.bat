@echo off
rem Build dist\SnapTrans\SnapTrans.exe with PyInstaller (clean isolated venv).
cd /d "%~dp0"
if not exist ".venv-build\Scripts\python.exe" (
  python -m venv .venv-build
  if errorlevel 1 goto fail
)
".venv-build\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 goto fail
".venv-build\Scripts\python.exe" tools\make_icon.py
if errorlevel 1 goto fail
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean snaptrans.spec
if errorlevel 1 goto fail
if exist "dist\SnapTrans-win64.zip" del "dist\SnapTrans-win64.zip"
tar -a -c -f "dist\SnapTrans-win64.zip" -C dist SnapTrans
if errorlevel 1 goto fail
echo.
echo [SnapTrans] Build OK: dist\SnapTrans\SnapTrans.exe
echo [SnapTrans] Zip:      dist\SnapTrans-win64.zip
pause
exit /b 0
:fail
echo.
echo [SnapTrans] Build failed, see the errors above.
pause
exit /b 1
