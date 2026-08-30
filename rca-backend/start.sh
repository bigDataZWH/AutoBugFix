#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[x] 未检测到 python3，请先安装 Python 3.10+"
  exit 1
fi

if [ ! -f ".venv/bin/python" ]; then
  echo "[*] 创建虚拟环境 ..."
  python3 -m venv .venv
fi

echo "[*] 安装依赖 ..."
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q

echo "[*] 启动 RCA Command 服务 http://localhost:8000"
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
