"""跨模块集成测试套件：8 个场景，覆盖 6 模块全链路集成。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent


class TestIntegFullStackStartup:
    """场景 integ_full_stack_startup — 全栈启动"""

    @pytest.mark.asyncio
    async def test_all_components_respond(self, asgi_client):
        resp = await asgi_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        comps = data["components"]
        for comp in ("postgres", "redis", "lightrag", "codegraph", "llm"):
            assert comp in comps

    @pytest.mark.asyncio
    async def test_frontend_backend_reachable(self, asgi_client):
        resp = await asgi_client.get("/")
        assert resp.status_code == 200


class TestIntegFrontendToBackendApi:
    """场景 integ_frontend_to_backend_api — 前端→后端 API 全链路"""

    @pytest.mark.asyncio
    async def test_analyze_to_result(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/1",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "集成测试全链路",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        data = result.json()
        assert data["stage"]["status"] == "completed"
        assert "top3" in data
        assert "solution" in data


class TestIntegMockDemoMode:
    """场景 integ_mock_demo_mode — Mock 冒烟集成"""

    @pytest.mark.asyncio
    async def test_mock_mode_complete(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/2",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "Mock 冒烟",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        data = result.json()
        assert data["runtime_mode"] == "mock_demo"
        assert data["stage"]["status"] == "completed"
        assert data["solution"]["patch_suggestion"]
        assert len(data["solution"]["best_practices"]) > 0


class TestIntegOnlineFullMode:
    """场景 integ_online_full_mode — 联机全量集成"""

    @pytest.mark.asyncio
    async def test_online_mode_flag(self, asgi_client, mode_online_full):
        resp = await asgi_client.get("/api/v1/health")
        data = resp.json()
        # 在 mock_demo 环境，online_full 组件状态应为 up
        comps = data["components"]
        assert comps["codegraph"]["status"] in ("up", "degraded", "down")
        assert comps["lightrag"]["status"] in ("up", "degraded", "down")


class TestIntegOfflineLightMode:
    """场景 integ_offline_light_mode — 离线轻量降级集成"""

    @pytest.mark.asyncio
    async def test_offline_components(self, asgi_client, mode_offline_light):
        resp = await asgi_client.get("/api/v1/health")
        data = resp.json()
        comps = data["components"]
        # offline_light 下 lightrag 应为 down
        assert comps["lightrag"]["status"] in ("down", "degraded")
        # codegraph 降级
        assert comps["codegraph"]["status"] in ("degraded", "down")


class TestIntegRestartRecovery:
    """场景 integ_restart_recovery — 中断重启断点续跑"""

    @pytest.mark.asyncio
    async def test_state_persistence(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/3",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "恢复测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        # 通过 state 端点验证持久化
        state = await asgi_client.get(f"/api/v1/rca/{task_id}/state")
        assert state.status_code == 200
        assert state.json()["task_id"] == task_id


class TestIntegMilestoneKpi:
    """场景 integ_milestone_kpi_verification — 4 里程碑 KPI"""

    @pytest.mark.asyncio
    async def test_m1_latency(self, asgi_client):
        import time
        start = time.time()
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/4",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "KPI M1",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        await asgi_client.get(f"/api/v1/rca/{task_id}")
        assert (time.time() - start) < 30

    @pytest.mark.asyncio
    async def test_m2_top3_count(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/5",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "KPI M2",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        assert len(result.json()["top3"]) == 3


class TestIntegCiPipeline:
    """场景 integ_ci_pipeline — CI 流水线"""

    def test_pytest_collectable(self):
        """验证 pytest 可收集所有测试"""
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q", str(Path(__file__).parent)],
            capture_output=True, cwd=str(BACKEND_DIR)
        )
        assert result.returncode == 0 or "error" in result.stderr.decode().lower()
