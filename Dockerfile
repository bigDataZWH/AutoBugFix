# ====== Stage 1: 构建前端 ======
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ====== Stage 2: 后端运行时 ======
FROM python:3.12-slim AS runtime

WORKDIR /app

# 系统依赖 (chromadb/onnxruntime 需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# 拷贝前端构建产物到 backend/frontend/dist (main.py 会自动发现)
COPY --from=frontend-build /build/dist ./frontend/dist

# 数据持久化目录
RUN mkdir -p data
VOLUME /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
