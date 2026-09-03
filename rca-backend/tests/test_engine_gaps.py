"""P0-B: 引擎测试缺口 — B6 LangGraph 路径、B7 任务列表端点。"""
from __future__ import annotations

import asyncio

import pytest

from app.engine import RCAEngine
from app.models import RCAState, BugInfo, AnomalyPath


def _make_state(**overrides) -> RCAState:
    defaults = dict(
        task_id="p0b-test-001",
        bug_info=BugInfo(
            bug_id="MSP-001",
            title="测试问题单",
            description="订单超时",
            stack=["OrderController.createOrder:64", "OrderLockService.acquire:127"],
            severity="P0",
            component="order-service",
            repo="test-repo",
            branch="master",
        ),
        P_runtime=AnomalyPath(functions=["OrderLockService.acquire"], runtime_anomaly=0.9),
        runtime_mode="mock_demo",
    )
    defaults.update(overrides)
    return RCAState(**defaults)


class TestB6LangGraphPath:
    """B6: LangGraph 路径 — _build_langgraph 编译图后端到端运行。"""

    def test_langgraph_available(self):
        engine = RCAEngine()
        assert engine._use_langgraph is True, "langgraph 应已安装并可用"

    def test_build_state_machine_returns_callable(self):
        engine = RCAEngine()
        sm = engine.build_state_machine()
        assert callable(sm), "build_state_machine 应返回可调用对象"

    def test_langgraph_runs_full_pipeline(self):
        engine = RCAEngine()
        state = _make_state(task_id="lg-full-001")
        sm = engine.build_state_machine()
        result = sm(state)
        assert result.stage.name == "COMPLETED"
        assert result.stage.status == "completed"

    def test_langgraph_populates_top3(self):
        engine = RCAEngine()
        state = _make_state(task_id="lg-top3-001")
        sm = engine.build_state_machine()
        result = sm(state)
        assert len(result.top3) > 0
        assert len(result.top3) <= 3

    def test_langgraph_produces_solution(self):
        engine = RCAEngine()
        state = _make_state(task_id="lg-sol-001")
        sm = engine.build_state_machine()
        result = sm(state)
        assert result.solution is not None
        assert result.solution.patch_suggestion != ""

    def test_langgraph_gate_status(self):
        engine = RCAEngine()
        state = _make_state(task_id="lg-gate-001")
        sm = engine.build_state_machine()
        result = sm(state)
        assert result.gate_status.crag in ("passed", "relevant", "ambiguous")
        assert result.gate_status.hil in ("skipped", "passed", "confirmed")

    def test_langgraph_consistent_with_sequential(self):
        """LangGraph 路径与 sequential 路径产出结构一致。"""
        engine = RCAEngine()
        state_lg = _make_state(task_id="lg-cmp-001")
        state_sq = _make_state(task_id="sq-cmp-001")

        sm = engine.build_state_machine()
        result_lg = sm(state_lg)
        result_sq = engine.run_sequential(state_sq)

        assert result_lg.stage.name == result_sq.stage.name
        assert len(result_lg.top3) == len(result_sq.top3)
        assert (result_lg.solution is not None) == (result_sq.solution is not None)


class TestB7TaskListEndpoint:
    """B7: /api/v1/rca/tasks 任务列表端点。"""

    @pytest.mark.asyncio
    async def test_empty_task_list(self, asgi_client):
        resp = await asgi_client.get("/api/v1/rca/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "statuses" in data
        assert isinstance(data["tasks"], list)
        assert isinstance(data["statuses"], dict)

    @pytest.mark.asyncio
    async def test_task_appears_after_submit(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/b7",
            "bug_desc": "B7 任务列表测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]

        list_resp = await asgi_client.get("/api/v1/rca/tasks")
        data = list_resp.json()
        assert task_id in data["tasks"]
        assert data["statuses"][task_id] in ("running", "done", "failed", "error")

    @pytest.mark.asyncio
    async def test_task_status_transitions(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/b7t",
            "bug_desc": "B7 状态流转测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]

        await asyncio.sleep(8)

        list_resp = await asgi_client.get("/api/v1/rca/tasks")
        data = list_resp.json()
        assert data["statuses"][task_id] in ("done", "failed", "error")
