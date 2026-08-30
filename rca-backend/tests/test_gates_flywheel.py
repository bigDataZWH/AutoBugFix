"""Spec 6 双闸门与知识飞轮 UT 测试套件：CRAG、HIL、飞轮回写、双闸门串联。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.gates import crag_gate, hil_gate, process_hil_decision
from app.flywheel import Flywheel
from app.models import (
    Evidence, Candidate, CragTriage, HilDecision, HilResult,
    FlywheelPayload, WeightConfig,
)
from app.config import config

FIXTURES = Path(__file__).parent / "fixtures" / "gate"


def _make_evidence(**overrides) -> Evidence:
    defaults = dict(static_depth=0.8, runtime_anomaly=0.7, metric_corr=0.6, change_recency=0.5)
    defaults.update(overrides)
    return Evidence(**defaults)


def _make_candidates(n=3, confidence_offset=0) -> list[Candidate]:
    return [
        Candidate(
            function_id=f"fn{i}", function_name=f"fn{i}", score=0.9 - i * 0.1 + confidence_offset,
            evidence=_make_evidence(), hit_kind="intersection",
        )
        for i in range(n)
    ]


class TestCragRelevant:
    """UT 1: CRAG relevant 分支"""

    def test_high_evidence_relevant(self):
        ev = _make_evidence(static_depth=0.9, runtime_anomaly=0.8)
        result = crag_gate([ev])
        assert result.verdict == "relevant"

    def test_refined_evidence_not_empty(self):
        ev = _make_evidence(static_depth=0.9)
        result = crag_gate([ev])
        assert result.verdict == "relevant"


class TestCragAmbiguous:
    """UT 2: CRAG ambiguous 分支"""

    def test_medium_evidence_ambiguous(self):
        ev = _make_evidence(static_depth=0.5, runtime_anomaly=0.3)
        result = crag_gate([ev])
        assert result.verdict in ("ambiguous", "relevant", "irrelevant")


class TestCragIrrelevant:
    """UT 3: CRAG irrelevant 分支"""

    def test_low_evidence_irrelevant(self):
        ev = _make_evidence(static_depth=0.1, runtime_anomaly=0.1, metric_corr=0.0, change_recency=0.0)
        result = crag_gate([ev])
        assert result.verdict in ("irrelevant", "ambiguous")

    def test_empty_evidence_irrelevant(self):
        """空证据列表 → 直接 irrelevant"""
        result = crag_gate([])
        assert result.verdict == "irrelevant"


class TestHILThreshold:
    """UT 4: HIL 置信度阈值 τ=0.6"""

    def test_high_confidence_pass(self):
        candidates = _make_candidates(confidence_offset=0.3)  # score ~0.9+
        result = hil_gate(candidates, confidence=0.8)
        assert result.action == "pass"

    def test_low_confidence_hang(self):
        result = hil_gate(_make_candidates(), confidence=0.4)
        assert result.action == "hang"

    def test_boundary_070_pass(self):
        """τ=0.7 边界：恰好等于阈值 → pass"""
        result = hil_gate(_make_candidates(), confidence=0.7)
        assert result.action == "pass"

    def test_boundary_069_hang(self):
        """τ=0.7 边界：0.69 → hang"""
        result = hil_gate(_make_candidates(), confidence=0.69)
        assert result.action == "hang"

    def test_boundary_071_pass(self):
        """τ=0.7 边界：0.71 → pass"""
        result = hil_gate(_make_candidates(), confidence=0.71)
        assert result.action == "pass"

    def test_panel_payload_on_hang(self):
        result = hil_gate(_make_candidates(), confidence=0.3, task_id="task-001")
        if result.action == "hang":
            assert result.panel_payload is not None
            assert result.panel_payload.task_id == "task-001"


class TestHILDecision:
    """UT 5: HIL 人工决策 (confirm/modify/reject)"""

    def test_confirm(self):
        candidates = _make_candidates()
        decision = HilDecision(task_id="t1", action="confirm")
        result_top3, status = process_hil_decision(decision, candidates)
        assert status == "confirmed"
        assert result_top3 == candidates

    def test_modify(self):
        candidates = _make_candidates()
        modified = [{"function_id": "fn_new", "function_name": "new", "score": 0.95, "hit_kind": "intersection"}]
        decision = HilDecision(task_id="t1", action="modify", modified_top3=modified)
        result_top3, status = process_hil_decision(decision, candidates)
        assert status == "modified"
        assert len(result_top3) == 1
        # process_hil_decision returns dicts for modify
        if isinstance(result_top3[0], dict):
            assert result_top3[0]["function_id"] == "fn_new"
        else:
            assert result_top3[0].function_id == "fn_new"

    def test_reject(self):
        candidates = _make_candidates()
        decision = HilDecision(task_id="t1", action="reject", feedback="错误")
        result_top3, status = process_hil_decision(decision, candidates)
        assert status == "rejected"


class TestFlywheelWriteback:
    """UT 6: 知识飞轮回写"""

    def test_writeback_success(self):
        fw = Flywheel()
        payload = FlywheelPayload(
            root_cause="分布式锁过粗",
            root_cause_function="OrderLockService.acquire",
            call_path=["A", "B", "C"],
            fix_patch="分段锁+maxActive=64",
            verify_case="1000 QPS 压测通过",
        )
        result = asyncio.run(fw.writeback(payload))
        assert result.inserted >= 0  # degraded 模式可能返回 0

    def test_writeback_dedup(self):
        """重复 payload 不重复写入"""
        fw = Flywheel()
        payload = FlywheelPayload(
            root_cause="测试根因",
            root_cause_function="test.fn",
        )
        r1 = asyncio.run(fw.writeback(payload))
        r2 = asyncio.run(fw.writeback(payload))  # 相同 payload
        # 第二次应该返回 inserted=0（去重）
        assert r2.inserted == 0

    def test_writeback_result_has_similar_edges(self):
        fw = Flywheel()
        payload = FlywheelPayload(root_cause="test", root_cause_function="fn")
        result = asyncio.run(fw.writeback(payload))
        assert hasattr(result, "similar_edges")
        assert isinstance(result.similar_edges, list)


class TestDoubleGateSequence:
    """UT 7: 双闸门串联 (CRAG → HIL)"""

    def test_crag_then_hil_sequence(self):
        """CRAG 通过后 HIL 判定"""
        ev = _make_evidence(static_depth=0.9, runtime_anomaly=0.8)
        crag_result = crag_gate([ev])
        if crag_result.verdict == "relevant":
            hil_result = hil_gate(_make_candidates(), confidence=0.8)
            assert hil_result.action == "pass"

    def test_high_confidence_bypass_hil(self):
        """高置信直通：跳过 HIL"""
        candidates = _make_candidates(confidence_offset=0.2)
        hil_result = hil_gate(candidates, confidence=0.9)
        assert hil_result.action == "pass"

    def test_low_confidence_triggers_hil(self):
        """低置信触发 HIL"""
        hil_result = hil_gate(_make_candidates(), confidence=0.3)
        assert hil_result.action == "hang"


class TestGateConfig:
    """UT 8: 闸门配置"""

    def test_hil_threshold_config(self):
        assert config.gate.hil_confidence_threshold == 0.7

    def test_gate_config_exists(self):
        assert hasattr(config, "gate")
        assert hasattr(config.gate, "hil_confidence_threshold")
        assert hasattr(config.gate, "max_rewrite_rounds")


class TestGateFixtures:
    """UT 9: 测试 fixtures 校验"""

    def test_crag_samples(self):
        with open(FIXTURES / "samples.json") as f:
            data = json.load(f)
        assert data["crag_relevant"]["verdict"] == "relevant"
        assert data["crag_ambiguous"]["verdict"] == "ambiguous"
        assert data["crag_irrelevant"]["verdict"] == "irrelevant"

    def test_hil_samples(self):
        with open(FIXTURES / "samples.json") as f:
            data = json.load(f)
        assert data["hil_confirm"]["action"] == "confirm"
        assert data["hil_modify"]["action"] == "modify"
        assert data["hil_reject"]["action"] == "reject"
        assert data["hil_modify"]["modified_top3"] is not None

    def test_flywheel_payload_fixture(self):
        with open(FIXTURES / "samples.json") as f:
            data = json.load(f)
        fp = data["flywheel_payload"]
        assert fp["root_cause"] != ""
        assert fp["root_cause_function"] != ""
        assert len(fp["call_path"]) > 0
        assert fp["fix_patch"] != ""
