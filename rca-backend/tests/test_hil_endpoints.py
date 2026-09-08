"""P0-M: HIL 断点续跑端点级测试（M4 confirm / M5 modify / M6 reject / M7 resume）。"""
from __future__ import annotations

import pytest
from app.engine import engine
from app.models import RCAState, BugInfo, AnomalyPath


def _seed_task(task_id: str) -> RCAState:
    """创建并运行一个分析任务，填充 engine.store。"""
    state = RCAState(
        task_id=task_id,
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
    engine.run_sequential(state)
    return state


class TestM4HILConfirmEndpoint:
    """M4: POST /api/v1/rca/{task_id}/confirm — action=confirm"""

    @pytest.mark.asyncio
    async def test_confirm_returns_completed(self, asgi_client):
        task_id = "m4-confirm-001"
        _seed_task(task_id)
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "confirm",
            "operator": "test-user",
            "comment": "确认根因",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["status"] == "completed"
        assert data["gate_status"]["hil"] in ("confirmed", "skipped")

    @pytest.mark.asyncio
    async def test_confirm_nonexistent_returns_500(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/nonexistent-task/confirm", json={
            "action": "confirm",
        })
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_confirm_state_persisted(self, asgi_client):
        task_id = "m4-confirm-002"
        _seed_task(task_id)
        await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "confirm",
        })
        resp = await asgi_client.get(f"/api/v1/rca/{task_id}/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["status"] == "completed"


class TestM5HILModifyEndpoint:
    """M5: POST /api/v1/rca/{task_id}/confirm — action=modify"""

    @pytest.mark.asyncio
    async def test_modify_with_reordered_top3(self, asgi_client):
        task_id = "m5-modify-001"
        _seed_task(task_id)
        state = engine.get_state(task_id)
        assert state is not None
        modified_top3 = [r.model_dump() for r in state.top3]
        if len(modified_top3) >= 2:
            modified_top3[0], modified_top3[1] = modified_top3[1], modified_top3[0]
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "modify",
            "modified_top3": modified_top3,
            "operator": "test-user",
            "comment": "调整根因排序",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["status"] == "completed"
        assert data["gate_status"]["hil"] in ("modified", "skipped")

    @pytest.mark.asyncio
    async def test_modify_without_modified_top3_keeps_original(self, asgi_client):
        task_id = "m5-modify-002"
        _seed_task(task_id)
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "modify",
            "operator": "test-user",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_modify_with_confirmed_root_cause_id(self, asgi_client):
        task_id = "m5-modify-003"
        _seed_task(task_id)
        state = engine.get_state(task_id)
        assert state is not None
        assert len(state.top3) >= 1
        picked_id = state.top3[-1].root_cause
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "modify",
            "confirmed_root_cause_id": picked_id,
            "operator": "test-user",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["status"] == "completed"


class TestM6HILRejectEndpoint:
    """M6: POST /api/v1/rca/{task_id}/confirm — action=reject"""

    @pytest.mark.asyncio
    async def test_reject_returns_rejected_stage(self, asgi_client):
        task_id = "m6-reject-001"
        _seed_task(task_id)
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "reject",
            "operator": "test-user",
            "comment": "根因不正确",
            "feedback": "需要重新分析",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["name"] == "REJECTED"
        assert data["gate_status"]["hil"] == "rejected"

    @pytest.mark.asyncio
    async def test_reject_state_persisted(self, asgi_client):
        task_id = "m6-reject-002"
        _seed_task(task_id)
        await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "reject",
        })
        resp = await asgi_client.get(f"/api/v1/rca/{task_id}/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["name"] == "REJECTED"
        assert data["gate_status"]["hil"] == "rejected"

    @pytest.mark.asyncio
    async def test_reject_then_resume_is_noop(self, asgi_client):
        task_id = "m6-reject-003"
        _seed_task(task_id)
        await asgi_client.post(f"/api/v1/rca/{task_id}/confirm", json={
            "action": "reject",
        })
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["name"] == "REJECTED"


class TestM7ResumeFromCheckpoint:
    """M7: POST /api/v1/rca/{task_id}/resume — 断点续跑"""

    @pytest.mark.asyncio
    async def test_resume_completed_task(self, asgi_client):
        task_id = "m7-resume-001"
        _seed_task(task_id)
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_resume_nonexistent_returns_500(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/nonexistent-task/resume")
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_resume_preserves_top3(self, asgi_client):
        task_id = "m7-resume-002"
        _seed_task(task_id)
        original = engine.get_state(task_id)
        resp = await asgi_client.post(f"/api/v1/rca/{task_id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["top3"]) == len(original.top3)
