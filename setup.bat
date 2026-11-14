@echo off
chcp 65001 >nul
rem 创建虚拟环境并安装依赖
cd /d "%~dp0"
if not exist ".venv" python -m venv .venv
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo 安装完成，双击 start.bat 启动。
pause
