from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动前确保数据目录就绪。"""
    settings.ensure_dirs()
    logger.info("AutoBugFix 后端已就绪: host=%s port=%s", settings.app_host, settings.app_port)
    yield


app = FastAPI(title="AutoBugFix 问题单解决平台", version="0.1.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务路由
app.include_router(router)

# 静态文件兜底: 若前端构建产物存在, 挂载到 "/" 承载 SPA (必须放在路由之后)
# 按优先级查找: 当前目录(开发/Docker) -> 上级目录(从 backend/ 启动) -> 模块同级
_app_dir = Path(__file__).resolve().parent.parent  # backend/
_candidates = [
    Path("frontend/dist"),
    _app_dir / "frontend" / "dist",
    _app_dir.parent / "frontend" / "dist",
]
_frontend_dist = next((p for p in _candidates if p.exists() and p.is_dir()), None)
if _frontend_dist:
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
else:

    @app.get("/")
    def root():
        """无前端构建产物时, 根路径返回欢迎信息。"""
        return {
            "name": "AutoBugFix 问题单解决平台",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/api/health",
            "message": "后端 API 已就绪, 前端构建产物(frontend/dist)未找到。",
        }
