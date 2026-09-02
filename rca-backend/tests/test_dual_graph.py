"""Spec 5 双图谱交叉验证 UT 测试套件：score 公式、Top-K 剪枝、降级、权重。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dual_graph import cross_validate, functions_of
from app.models import (
    SuspectFunction, AnomalyPath, MetricAnomalies, ChangeRecord,
    ChangeRecords, Candidate, Evidence,
)
from app.config import config, ScoreWeights

FIXTURES = Path(__file__).parent / "fixtures" / "dualgraph"


def _make_s_static() -> list[SuspectFunction]:
    return [
        SuspectFunction(function_id="OrderLockService.acquire", function_name="acquire",
                        call_path=["A", "B", "OrderLockService.acquire"], static_depth=3,
                        file="OrderLockService.java", line=127),
        SuspectFunction(function_id="JedisPool.getResource", function_name="getResource",
                        call_path=["OrderLockService.acquire", "JedisPool.getResource"], static_depth=4,
                        file="JedisPool.java", line=210),
        SuspectFunction(function_id="InventoryService.deduct", function_name="deduct",
                        call_path=["OrderService.submit", "InventoryService.deduct"], static_depth=2,
                        file="InventoryService.java", line=48),
    ]


def _make_p_runtime() -> AnomalyPath:
    return AnomalyPath(
        span_tree={"span-001": {"operation": "OrderService.create", "duration_ms": 3500, "error": True}},
        propagation_path=["OrderService.create", "OrderLockService.acquire", "JedisPool.getResource"],
        functions=["OrderLockService.acquire", "JedisPool.getResource"],
        runtime_anomaly=0.9,
    )


def _make_metrics() -> MetricAnomalies:
    return MetricAnomalies(
        functions={"OrderLockService.acquire": 0.7, "JedisPool.getResource": 0.6},
        services={"order-service": 0.8},
    )


def _make_changes() -> ChangeRecords:
    return ChangeRecords(records=[
        ChangeRecord(function_id="OrderLockService.acquire", timestamp=1723766400, commits=3),
    ])


class TestSStaticSchema:
    """UT 1: S_static 数据结构"""

    def test_fields(self):
        sf = SuspectFunction(function_id="fn1", function_name="fn", call_path=["a", "b"], static_depth=2)
        assert sf.function_id == "fn1"
        assert sf.static_depth == 2
        assert len(sf.call_path) == 2

    def test_defaults(self):
        sf = SuspectFunction(function_id="fn1")
        assert sf.function_name == ""
        assert sf.static_depth == 0.0
        assert sf.file == ""


class TestPRuntimeSchema:
    """UT 2: P_runtime 数据结构"""

    def test_fields(self):
        pr = AnomalyPath(
            functions=["fn1", "fn2"],
            runtime_anomaly=0.85,
        )
        assert "fn1" in pr.functions
        assert pr.runtime_anomaly == 0.85

    def test_defaults(self):
        pr = AnomalyPath()
        assert pr.functions == []
        assert pr.span_tree == {}


class TestFunctionsOf:
    """UT 3: functions_of 辅助函数"""

    def test_returns_set(self):
        pr = AnomalyPath(functions=["fn1", "fn2"])
        result = functions_of(pr)
        assert isinstance(result, set)
        assert "fn1" in result

    def test_empty(self):
        pr = AnomalyPath()
        assert functions_of(pr) == set()


class TestCrossValidateIntersection:
    """UT 4: 交集命中 — S_static ∩ P_runtime.functions"""

    def test_intersection_produces_candidates(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime())
        assert len(candidates) > 0

    def test_intersection_candidates_are_high_confidence(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime())
        intersection = [c for c in candidates if c.hit_kind == "intersection"]
        assert len(intersection) > 0

    def test_correct_functions_identified(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime())
        func_ids = [c.function_id for c in candidates]
        assert "OrderLockService.acquire" in func_ids
        assert "JedisPool.getResource" in func_ids


class TestScoreFormula:
    """UT 5: score 四维加权"""

    def test_score_in_range(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime(), _make_metrics(), _make_changes())
        for c in candidates:
            assert c.score >= 0.0

    def test_score_with_all_dimensions(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime(), _make_metrics(), _make_changes())
        top = candidates[0]
        assert top.score > 0.0

    def test_evidence_four_dimensions(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime(), _make_metrics(), _make_changes())
        for c in candidates:
            assert hasattr(c.evidence, "static_depth")
            assert hasattr(c.evidence, "runtime_anomaly")
            assert hasattr(c.evidence, "metric_corr")
            assert hasattr(c.evidence, "change_recency")


class TestTopKPruning:
    """UT 6: Top-K 剪枝 (默认 K=3)"""

    def test_top3_max(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime(), _make_metrics(), _make_changes())
        assert len(candidates) <= 3

    def test_sorted_descending(self):
        candidates = cross_validate(_make_s_static(), _make_p_runtime(), _make_metrics(), _make_changes())
        if len(candidates) >= 2:
            assert candidates[0].score >= candidates[1].score


class TestWeightConfig:
    """UT 7: 权重配置"""

    def test_default_weights(self):
        assert config.score_weights.w1 == 0.3
        assert config.score_weights.w2 == 0.3
        assert config.score_weights.w3 == 0.2
        assert config.score_weights.w4 == 0.2

    def test_custom_weights(self):
        w = ScoreWeights(w1=0.5, w2=0.3, w3=0.1, w4=0.1)
        candidates = cross_validate(_make_s_static(), _make_p_runtime(), _make_metrics(), _make_changes(), w)
        assert len(candidates) > 0

    def test_hil_default_weights(self):
        w = ScoreWeights.hil_default()
        assert w.w1 == 0.35
        assert w.w2 == 0.30
        assert w.w3 == 0.20
        assert w.w4 == 0.15


class TestDegradation:
    """UT 8: 降级场景"""

    def test_static_only_degrade(self):
        """P_runtime 为空 → static_only 候选"""
        empty_runtime = AnomalyPath()
        candidates = cross_validate(_make_s_static(), empty_runtime)
        static_only = [c for c in candidates if c.hit_kind == "static_only"]
        assert len(static_only) > 0

    def test_runtime_only_degrade(self):
        """S_static 为空 → 无候选（函数级候选依赖 S_static）"""
        candidates = cross_validate([], _make_p_runtime())
        assert len(candidates) == 0

    def test_both_empty(self):
        """两者为空 → 空候选"""
        candidates = cross_validate([], AnomalyPath())
        assert len(candidates) == 0

    def test_no_metrics(self):
        """Metric 缺失 → 仍产出候选"""
        candidates = cross_validate(_make_s_static(), _make_p_runtime())
        assert len(candidates) > 0

    def test_no_change_records(self):
        """变更记录缺失 → 仍产出候选"""
        candidates = cross_validate(_make_s_static(), _make_p_runtime(), _make_metrics())
        assert len(candidates) > 0


class TestEmptyIntersection:
    """UT 9: 交集为空时降级策略"""

    def test_no_intersection_uses_single_path(self):
        """S_static 与 P_runtime 无交集 → 单路补位"""
        s_static = [SuspectFunction(function_id="fn_unique", static_depth=2)]
        p_runtime = AnomalyPath(functions=["fn_other"])
        candidates = cross_validate(s_static, p_runtime)
        assert len(candidates) > 0
        for c in candidates:
            assert c.hit_kind in ("static_only", "runtime_only")


class TestDualGraphFixtures:
    """UT 10: 测试 fixtures 校验"""

    def test_samples_fixture(self):
        with open(FIXTURES / "samples.json") as f:
            data = json.load(f)
        assert len(data["s_static"]) == 3
        assert data["s_static"][0]["func_id"] == "OrderLockService.acquire"
        assert len(data["p_runtime"]["functions"]) == 2
        assert data["weights"]["top_k"] == 3
