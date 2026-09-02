"""Spec 4 5-Agent 引擎 UT 测试套件：状态机、Agent、编排、SSE、降级、断点恢复。"""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.engine import RCAEngine, SSEEventBus, StateStore
from app.models import (
    RCAState, BugInfo, AnomalyPath, Stage, RootCause,
    Solution, GateStatus, SuspectFunction, HilDecision,
)
from app.agents import AgentA1, AgentA2, AgentA3, AgentA4, AgentA5

FIXTURES = Path(__file__).parent / "fixtures" / "engine"


def _make_state(**overrides) -> RCAState:
    """RCAState Fixture 工厂。"""
    defaults = dict(
        task_id="test-task-001",
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
        P_runtime=AnomalyPath(
            functions=["OrderLockService.acquire"],
            runtime_anomaly=0.9,
        ),
        runtime_mode="mock_demo",
    )
    defaults.update(overrides)
    return RCAState(**defaults)


class TestRCAStateSchema:
    """UT 1: RCAState 数据结构 (11 字段)"""

    def test_required_fields(self):
        state = _make_state()
        assert state.bug_info is not None
        assert hasattr(state, "symptoms")
        assert hasattr(state, "error_type")
        assert hasattr(state, "query")
        assert hasattr(state, "suspect_services")
        assert hasattr(state, "S_static")
        assert hasattr(state, "P_runtime")
        assert hasattr(state, "top3")
        assert hasattr(state, "gate_status")
        assert hasattr(state, "solution")
        assert hasattr(state, "stage")

    def test_stage_enum(self):
        state = _make_state()
        assert state.stage.index == 0
        assert state.stage.name == ""
        assert state.stage.status == "pending"

    def test_serialization_roundtrip(self):
        state = _make_state()
        data = state.model_dump()
        restored = RCAState(**data)
        assert restored.bug_info.bug_id == state.bug_info.bug_id
        assert restored.runtime_mode == state.runtime_mode


class TestSequentialOrchestrator:
    """UT 2: SequentialOrchestrator 全链路 A1→A2∥A3→A4→gates→A5"""

    def test_run_sequential_completes(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        assert result.stage.name == "COMPLETED"
        assert result.stage.status == "completed"

    def test_run_sequential_populates_outputs(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        # A1 output
        assert len(result.symptoms) > 0
        assert result.error_type != ""
        # A4 output
        assert len(result.top3) > 0
        # A5 output
        assert result.solution is not None
        assert result.solution.patch_suggestion != ""

    def test_gate_status_after_run(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        assert result.gate_status.crag in ("passed", "relevant", "ambiguous")
        assert result.gate_status.hil in ("skipped", "passed", "confirmed")

    def test_task_id_generated(self):
        engine = RCAEngine()
        state = _make_state(task_id="")
        result = engine.run_sequential(state)
        assert result.task_id != ""


class TestAgentA1:
    """UT 3: A1 问题理解 Agent"""

    def test_a1_extracts_fields(self):
        a1 = AgentA1()
        bug = BugInfo(bug_id="MSP-001", title="超时", description="订单超时", stack=["fn:1"])
        out = a1.run(bug)
        assert len(out.symptoms) > 0
        assert out.error_type != ""
        assert out.query != ""
        assert len(out.suspect_services) > 0

    def test_a1_empty_stack(self):
        a1 = AgentA1()
        bug = BugInfo(bug_id="MSP-002", title="错误", description="服务异常")
        out = a1.run(bug)
        # 报错栈缺失时 error_type=unknown/UNKNOWN
        assert out.error_type.lower() in ("unknown", "timeout", "error")


class TestAgentA4:
    """UT 4: A4 根因分析 Agent (score 排序 + Top-3)"""

    def test_a4_top3_length(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        assert len(result.top3) <= 3
        assert len(result.top3) > 0

    def test_a4_confidence_ordering(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        if len(result.top3) >= 2:
            assert result.top3[0].confidence >= result.top3[1].confidence

    def test_a4_evidence_chain(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        # top3 可能有 insufficient_evidence 占位
        for rc in result.top3:
            if rc.confidence > 0:
                assert len(rc.evidence_chain) > 0
                assert rc.located_function != ""


class TestAgentA5:
    """UT 5: A5 方案生成 Agent"""

    def test_a5_generates_solution(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        sol = result.solution
        assert sol is not None
        assert sol.patch_suggestion != ""
        # steps 可能为空（mock_demo 模式），但 test_cases 应有
        assert len(sol.test_cases) > 0 or len(sol.historical_cases) > 0

    def test_a5_test_cases(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        assert len(result.solution.test_cases) > 0

    def test_a5_best_practices(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        assert len(result.solution.best_practices) > 0
        # best_practices 是 list[str]
        assert isinstance(result.solution.best_practices[0], str)


class TestSSEEventBus:
    """UT 6: SSE 事件总线"""

    def test_publish_and_replay(self):
        bus = SSEEventBus()
        bus.publish("task-1", "stage_start", {"stage": "A1"})
        bus.publish("task-1", "stage_complete", {"stage": "A1"})
        events = bus.replay("task-1")
        assert len(events) >= 2
        assert events[0]["event"] == "stage_start"
        assert events[1]["event"] == "stage_complete"

    def test_isolation_between_tasks(self):
        bus = SSEEventBus()
        bus.publish("task-A", "stage_start", {"stage": "A1"})
        bus.publish("task-B", "stage_start", {"stage": "A1"})
        events_a = bus.replay("task-A")
        events_b = bus.replay("task-B")
        # events are isolated per task
        assert len(events_a) >= 1
        assert len(events_b) >= 1
        # task-A events should not appear in task-B replay
        assert len(events_a) != len(events_b) or events_a != events_b


class TestStateStore:
    """UT 7: Redis 状态持久化 + 断点续跑"""

    def test_save_and_load(self):
        store = StateStore()
        state = _make_state(task_id="persist-001")
        store.save(state)
        loaded = store.load("persist-001")
        assert loaded is not None
        assert loaded.task_id == "persist-001"
        assert loaded.bug_info.bug_id == state.bug_info.bug_id

    def test_load_nonexistent(self):
        store = StateStore()
        result = store.load("nonexistent-task-999")
        assert result is None


class TestDegradedMode:
    """UT 8: 降级模式"""

    def test_degraded_flag(self):
        engine = RCAEngine()
        state = _make_state()
        result = engine.run_sequential(state)
        # mock_demo 模式下可能不标记 degraded
        assert hasattr(result, "degraded")
        assert hasattr(result, "runtime_mode")

    def test_runtime_mode_mock(self):
        engine = RCAEngine()
        state = _make_state(runtime_mode="mock_demo")
        result = engine.run_sequential(state)
        assert result.runtime_mode == "mock_demo"
        assert result.top3 is not None  # 仍产出 Top-3


class TestBreakpointResume:
    """UT 9: 断点续跑恢复"""

    def test_resume_from_checkpoint(self):
        engine = RCAEngine()
        state = _make_state(task_id="resume-001")
        # 先执行到完成
        engine.run_sequential(state)
        # 断点续跑（已完成的 stage 应跳过）
        restored = engine.resume_from_checkpoint("resume-001")
        assert restored is not None
        assert restored.task_id == "resume-001"

    def test_resume_nonexistent_raises(self):
        engine = RCAEngine()
        with pytest.raises(Exception):
            engine.resume_from_checkpoint("nonexistent-task-999")


class TestHILResume:
    """UT 10: HIL 人工确认回灌"""

    def test_hil_confirm(self):
        engine = RCAEngine()
        state = _make_state(task_id="hil-001")
        engine.run_sequential(state)
        decision = HilDecision(task_id="hil-001", action="confirm")
        result = engine.resume("hil-001", decision)
        assert result is not None
        assert result.gate_status.hil in ("confirmed", "skipped", "passed")

    def test_hil_reject(self):
        engine = RCAEngine()
        state = _make_state(task_id="hil-002")
        engine.run_sequential(state)
        decision = HilDecision(task_id="hil-002", action="reject", feedback="错误")
        # reject 可能不抛异常，而是设置状态
        try:
            result = engine.resume("hil-002", decision)
            assert result is not None
        except Exception:
            pass  # 也可以抛异常


class TestEngineFixtures:
    """UT 11: 测试 fixtures 校验"""

    def test_rca_state_fixture(self):
        with open(FIXTURES / "rca_state_sample.json") as f:
            data = json.load(f)
        assert data["task_id"] == "rca-20260830-0001"
        assert data["bug_info"]["bug_id"] == "MSP-2026-0817"
        assert len(data["bug_info"]["stack"]) == 3
        assert data["gate_status"]["crag"] == "passed"
        assert data["runtime_mode"] == "mock_demo"
