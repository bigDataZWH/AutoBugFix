@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PORT=%RCA_PORT%"
if "%PORT%"=="" set "PORT=8000"

REM --- 前置检查：Python 版本 ---
where python >nul 2>nul
if errorlevel 1 (
  echo [x] 未检测到 python，请先安装 Python 3.10+ 并加入 PATH
  pause
  exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set "PY_VER=%%i"
python -c "import sys; sys.exit(0 if (sys.version_info.major, sys.version_info.minor) >= (3, 10) else 1)" 2>nul
if errorlevel 1 (
  echo [x] Python %PY_VER% 低于最低要求 3.10
  pause
  exit /b 1
)
echo [+] Python %PY_VER%

REM --- 端口检测 ---
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
  echo [x] 端口 %PORT% 已被占用，请使用 set RCA_PORT=其他端口 后再运行
  pause
  exit /b 1
)

REM --- 虚拟环境（增量复用） ---
if not exist ".venv\Scripts\python.exe" (
  echo [*] 创建虚拟环境 ...
  python -m venv .venv
)

REM --- 依赖安装（失败兜底） ---
echo [*] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo [x] 依赖安装失败，请检查网络或使用离线 wheel
  echo     可尝试: set RCA_RUNTIME_MODE=mock_demo（仅核心依赖）
  pause
  exit /b 1
)

REM --- 运行模式 ---
if "%RCA_RUNTIME_MODE%"=="" set "RCA_RUNTIME_MODE=mock_demo"
echo [*] 运行模式: %RCA_RUNTIME_MODE%
echo [*] 启动 RCA Command 服务 http://localhost:%PORT%
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%
endlocal
