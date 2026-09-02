#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${RCA_PORT:-8000}"

# --- 前置检查 ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "[x] 未检测到 python3，请先安装 Python 3.10+"
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 -c 'import sys; sys.exit(0 if (sys.version_info.major, sys.version_info.minor) >= (3, 10) else 1)'; then
  echo "[+] Python $PY_VER"
else
  echo "[x] Python $PY_VER 低于最低要求 3.10"
  exit 1
fi

# --- 端口检测 ---
if command -v ss >/dev/null 2>&1; then
  if ss -tlnp 2>/dev/null | grep -q ":${PORT} " ; then
    echo "[x] 端口 ${PORT} 已被占用，请使用 RCA_PORT=其他端口 bash start.sh"
    exit 1
  fi
elif command -v lsof >/dev/null 2>&1; then
  if lsof -i :"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[x] 端口 ${PORT} 已被占用，请使用 RCA_PORT=其他端口 bash start.sh"
    exit 1
  fi
fi

# --- 虚拟环境（增量复用） ---
if [ ! -f ".venv/bin/python" ]; then
  echo "[*] 创建虚拟环境 ..."
  python3 -m venv .venv
fi

# --- 依赖安装（失败兜底） ---
echo "[*] 安装依赖 ..."
.venv/bin/python -m pip install --upgrade pip -q
if ! .venv/bin/python -m pip install -r requirements.txt -q 2>&1; then
  echo "[x] 依赖安装失败，请检查网络或使用离线 wheel"
  echo "    可尝试: RCA_RUNTIME_MODE=mock_demo（仅核心依赖）"
  exit 1
fi

# --- 运行模式 ---
export RCA_RUNTIME_MODE="${RCA_RUNTIME_MODE:-mock_demo}"
echo "[*] 运行模式: ${RCA_RUNTIME_MODE}"
echo "[*] 启动 RCA Command 服务 http://localhost:${PORT}"
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
