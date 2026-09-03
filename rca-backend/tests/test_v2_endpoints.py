"""P1-E: V2 Pipeline 端点级测试（E1/E2/E5/E6）。

验证 V2 分析端点的完整生命周期：
- E1: POST /api/analyze — 创建分析任务
- E2: GET /api/analyze/{task_id}/stream — SSE 事件流
- E5: GET /api/analyze/{task_id} — 获取报告
- E6: GET /api/health — 基础健康检查
"""
from __future__ import annotations

import asyncio
import json

import pytest


def _sse_events(resp_text: str) -> list[dict]:
    """从 SSE 响应文本中解析事件列表。"""
    events: list[dict] = []
    for line in resp_text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ============================================================================
# E6: GET /api/health — 基础健康检查
# ============================================================================

class TestE6HealthEndpoint:
    """GET /api/health 返回服务状态与版本。"""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, asgi_client):
        resp = await asgi_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_health_returns_kb_count(self, asgi_client):
        resp = await asgi_client.get("/api/health")
        data = resp.json()
        assert "kb_count" in data
        assert isinstance(data["kb_count"], int)

    @pytest.mark.asyncio
    async def test_health_returns_opencode_status(self, asgi_client):
        resp = await asgi_client.get("/api/health")
        data = resp.json()
        assert "opencode_available" in data
        assert isinstance(data["opencode_available"], bool)


# ============================================================================
# E1: POST /api/analyze — 创建分析任务
# ============================================================================

class TestE1AnalyzeEndpoint:
    """POST /api/analyze 创建 V2 分析任务并返回 task_id。"""

    @pytest.mark.asyncio
    async def test_valid_request_returns_task_id(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "ticket_url": "https://jira.example.com/ISSUE-42",
            "repo_url": "https://github.com/demo/repo",
            "branch": "main",
            "description": "库存超卖问题",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["task_id"].startswith("v2-")

    @pytest.mark.asyncio
    async def test_missing_ticket_and_description_returns_400(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "repo_url": "https://github.com/demo/repo",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_only_description_no_ticket_url(self, asgi_client):
        """仅提供 description（无 ticket_url）也应接受。"""
        resp = await asgi_client.post("/api/analyze", json={
            "repo_url": "https://github.com/demo/repo",
            "description": "库存超卖问题",
        })
        assert resp.status_code == 200
        assert "task_id" in resp.json()


# ============================================================================
# E2: GET /api/analyze/{task_id}/stream — SSE 事件流
# ============================================================================

class TestE2StreamEndpoint:
    """GET /api/analyze/{task_id}/stream 返回 SSE 事件流。"""

    @pytest.mark.asyncio
    async def test_stream_produces_stage_and_report_events(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "ticket_url": "https://jira.example.com/ISSUE-42",
            "repo_url": "https://github.com/demo/repo",
            "description": "库存超卖问题",
        })
        task_id = resp.json()["task_id"]

        stream_resp = await asgi_client.get(f"/api/analyze/{task_id}/stream")
        assert stream_resp.status_code == 200
        events = _sse_events(stream_resp.text)

        types = [e["type"] for e in events]
        stage_events = [e for e in events if e["type"] == "stage"]
        assert len(stage_events) >= 12, (
            f"应至少有 12 个 stage 事件（6 阶段 × active+done），实际 {len(stage_events)}"
        )
        assert "report" in types or "error" in types, (
            "SSE 流应以 report 或 error 事件结束"
        )

    @pytest.mark.asyncio
    async def test_stream_nonexistent_returns_404(self, asgi_client):
        resp = await asgi_client.get("/api/analyze/nonexistent-task/stream")
        assert resp.status_code == 404


# ============================================================================
# E5: GET /api/analyze/{task_id} — 获取报告
# ============================================================================

class TestE5ReportEndpoint:
    """GET /api/analyze/{task_id} 获取分析报告。"""

    @pytest.mark.asyncio
    async def test_report_after_completion(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "ticket_url": "https://jira.example.com/ISSUE-42",
            "repo_url": "https://github.com/demo/repo",
            "description": "库存超卖问题",
        })
        task_id = resp.json()["task_id"]

        stream_resp = await asgi_client.get(f"/api/analyze/{task_id}/stream")
        events = _sse_events(stream_resp.text)

        report_resp = await asgi_client.get(f"/api/analyze/{task_id}")
        assert report_resp.status_code == 200
        report = report_resp.json()
        assert "rca" in report
        assert "solution" in report
        assert "confidence" in report
        assert isinstance(report["confidence"], (int, float))

    @pytest.mark.asyncio
    async def test_report_nonexistent_returns_404(self, asgi_client):
        resp = await asgi_client.get("/api/analyze/nonexistent-task")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_report_not_finished_returns_409(self, asgi_client):
        from app.main import tasks
        tasks["test-409-task"] = {
            "queue": asyncio.Queue(),
            "report": None,
            "status": "running",
        }
        try:
            resp = await asgi_client.get("/api/analyze/test-409-task")
            assert resp.status_code == 409
        finally:
            tasks.pop("test-409-task", None)
