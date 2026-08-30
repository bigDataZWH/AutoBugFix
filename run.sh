#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

MODE="${1:-prod}"

echo "========================================"
echo "  AutoBugFix 问题单解决平台"
echo "  模式: $MODE"
echo "========================================"

# 确保 .env 存在
if [ ! -f "$BACKEND/.env" ]; then
  echo "[配置] 未找到 backend/.env, 从 .env.example 复制 ..."
  cp "$BACKEND/.env.example" "$BACKEND/.env"
  echo "[配置] 请按需编辑 backend/.env (LLM/CodeHub/Embedding)"
fi

if [ "$MODE" = "dev" ]; then
  # ---- 开发模式: 前后端分离 + 热更新 ----
  echo "[后端] 安装依赖 ..."
  cd "$BACKEND" && pip install -q -r requirements.txt

  echo "[前端] 安装依赖 ..."
  cd "$FRONTEND" && npm install --silent

  echo ""
  echo "[启动] 后端 http://localhost:8000  (API + docs)"
  echo "[启动] 前端 http://localhost:5173  (Vite dev, /api 代理到 8000)"
  echo ""

  # 同时启动前后端
  cd "$BACKEND" && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
  BACKEND_PID=$!
  cd "$FRONTEND" && npm run dev &
  FRONTEND_PID=$!

  trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
  wait

elif [ "$MODE" = "ingest" ]; then
  # ---- 入库模式: 批量导入知识库 ----
  SAMPLE="$BACKEND/data/samples/knowledge_base.json"
  TARGET="${2:-$SAMPLE}"
  echo "[入库] 导入: $TARGET"
  cd "$BACKEND" && python scripts/ingest_kb.py "$TARGET"

elif [ "$MODE" = "prod" ]; then
  # ---- 生产模式: 前端构建 + 后端单端口 ----
  echo "[前端] 安装依赖 + 构建 ..."
  cd "$FRONTEND" && npm install --silent && npm run build

  echo "[后端] 安装依赖 ..."
  cd "$BACKEND" && pip install -q -r requirements.txt

  echo ""
  echo "[启动] http://localhost:8000  (前后端统一端口)"
  echo ""
  cd "$BACKEND" && uvicorn app.main:app --host 0.0.0.0 --port 8000

else
  echo "用法: ./run.sh [prod|dev|ingest] [知识库文件路径]"
  echo "  prod    构建 frontend + 启动 backend (默认, 单端口 8000)"
  echo "  dev     前后端分离热更新 (前端 5173, 后端 8000)"
  echo "  ingest  批量导入知识库 (默认导入样例, 可指定文件路径)"
  exit 1
fi
