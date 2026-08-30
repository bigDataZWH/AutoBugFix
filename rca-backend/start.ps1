@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] 未检测到 python，请先安装 Python 3.10+ 并加入 PATH
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [*] 创建虚拟环境 ...
  python -m venv .venv
)

echo [*] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q

echo [*] 启动 RCA Command 服务 http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
endlocal
