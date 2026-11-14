@echo off
chcp 65001 >nul
rem 启动 SnapTrans（无控制台窗口）。首次使用先运行 setup.bat
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 请先运行 setup.bat 安装依赖
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0run.pyw"
