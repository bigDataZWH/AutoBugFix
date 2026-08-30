"""pytest 公共 fixtures：ASGI client、降级模式 fixture、Bug 单样本。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
import httpx

# 将 rca-backend 加入 sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

FIXTURES = Path(__file__).parent / "fixtures" / "deploy"


@pytest.fixture
def fixture_dir():
    return FIXTURES


@pytest.fixture
def env_matrix():
    with open(FIXTURES / "env_matrix.json") as f:
        return json.load(f)


@pytest.fixture
def degradation_matrix():
    with open(FIXTURES / "degradation_matrix.json") as f:
        return json.load(f)


@pytest.fixture
def bug_ticket_001():
    with open(FIXTURES / "bug_tickets" / "bug_001.json") as f:
        return json.load(f)


@pytest.fixture
def bug_ticket_002():
    with open(FIXTURES / "bug_tickets" / "bug_002.json") as f:
        return json.load(f)


@pytest.fixture
def mode_online_full(monkeypatch):
    monkeypatch.setenv("RCA_RUNTIME_MODE", "online_full")
    # 重置运行时模式缓存
    from app import runtime_mode
    runtime_mode._current_mode = None
    return "online_full"


@pytest.fixture
def mode_offline_light(monkeypatch):
    monkeypatch.setenv("RCA_RUNTIME_MODE", "offline_light")
    from app import runtime_mode
    runtime_mode._current_mode = None
    return "offline_light"


@pytest.fixture
def mode_mock_demo(monkeypatch):
    monkeypatch.setenv("RCA_RUNTIME_MODE", "mock_demo")
    from app import runtime_mode
    runtime_mode._current_mode = None
    return "mock_demo"


@pytest_asyncio.fixture
async def asgi_client():
    """ASGI 测试客户端，直连 FastAPI app。"""
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def rca_command_html():
    """前端 HTML 源码。"""
    html_path = BACKEND_DIR.parent / "rca-command.html"
    with open(html_path, encoding="utf-8") as f:
        return f.read()
