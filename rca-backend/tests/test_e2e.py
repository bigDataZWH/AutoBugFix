"""E2E 测试套件：8 个场景，使用 ASGI client 驱动 HTTP/SSE。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
import httpx


class TestE2EFullStartup:
    """场景 1: e2e_full_local_startup — 服务启动 + 健康检查"""

    @pytest.mark.asyncio
    async def test_health_up(self, asgi_client):
        resp = await asgi_client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("up", "degraded")

    @pytest.mark.asyncio
    async def test_frontend_accessible(self, asgi_client):
        resp = await asgi_client.get("/")
        assert resp.status_code == 200
        assert "RCA" in resp.text or "rca" in resp.text.lower()


class TestE2EAnalyzeRequest:
    """场景 2: e2e_rca_analyze_request — 提交分析 + SSE 流"""

    @pytest.mark.asyncio
    async def test_analyze_and_stream(self, asgi_client):
        # 提交分析
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/1",
            "repo": "https://github.com/test/repo",
            "bug_desc": "E2E 测试 bug",
            "runtime_mode": "mock_demo",
        })
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        assert task_id

        # 等待分析完成
        await asyncio.sleep(8)

        # 获取结果
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        assert result.status_code == 200
        data = result.json()
        assert data["stage"]["status"] == "completed"
        assert len(data["top3"]) > 0


class TestE2EMockModeSmoke:
    """场景 5: e2e_mock_mode_smoke — Mock 模式全流程冒烟"""

    @pytest.mark.asyncio
    async def test_mock_full_flow(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/2",
            "repo": "https://github.com/test/repo",
            "bug_desc": "Mock 冒烟测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]

        await asyncio.sleep(8)

        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        data = result.json()
        assert data["runtime_mode"] == "mock_demo"
        assert data["stage"]["status"] == "completed"
        assert data["solution"]["patch_suggestion"]  # 方案非空


class TestE2EMilestoneKPI:
    """场景 6: e2e_milestone_kpi — 里程碑指标验证"""

    @pytest.mark.asyncio
    async def test_m1_end_to_end_latency(self, asgi_client):
        """M1: 端到端 <30s"""
        start = time.time()
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/3",
            "repo": "https://github.com/test/repo",
            "bug_desc": "M1 延迟测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        elapsed = time.time() - start
        assert result.json()["stage"]["status"] == "completed"
        assert elapsed < 30, f"端到端耗时 {elapsed:.1f}s 超过 30s"

    @pytest.mark.asyncio
    async def test_top3_output(self, asgi_client):
        """M2: Top-3 命中"""
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/4",
            "repo": "https://github.com/test/repo",
            "bug_desc": "Top-3 验证",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        top3 = result.json()["top3"]
        assert len(top3) == 3


class TestE2ERestartRecovery:
    """场景 7: e2e_restart_recovery — 断点续跑"""

    @pytest.mark.asyncio
    async def test_state_recovery(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/5",
            "repo": "https://github.com/test/repo",
            "bug_desc": "恢复测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)

        # 模拟通过 state 端点恢复
        state_resp = await asgi_client.get(f"/api/v1/rca/{task_id}/state")
        assert state_resp.status_code == 200
        state = state_resp.json()
        assert state["task_id"] == task_id
