"""TDD: V2 Pipeline _step_synthesize 合成阶段测试套件。

验证 _step_synthesize 从 RAG 匹配结果 + 调用链数据合成上下文，
且 _assemble 消费该上下文填充 Solution 的 patch_suggestion /
historical_cases / best_practices 等字段。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models import (
    AnalysisReport, AnalyzeRequestV2, BestPractice, CallStackNode, KBMatch,
)
from app.pipeline import Pipeline


@pytest.fixture
def pipeline():
    retriever = MagicMock()
    opencode = MagicMock()
    return Pipeline(retriever=retriever, opencode=opencode)


@pytest.fixture
def req():
    return AnalyzeRequestV2(
        ticket_url="https://jira.example.com/ISSUE-42",
        repo_url="https://github.com/demo/repo",
        branch="main",
        description="库存超卖问题",
    )


@pytest.fixture
def kb_matches():
    return [
        KBMatch(
            similarity=0.92, ticket_id="KB-001",
            title="Redis 分布式锁粒度过粗导致超卖",
            root_cause="OrderLockService.acquire 使用单 key 全量锁，高并发竞争",
            fix_code="int seg = Math.floorMod(skuId.hashCode(), 16);\n"
                     'return redis.setnx("lock:" + seg + ":" + skuId, "1", 30);',
            microservice="order-svc", error_code="OVERSELL_001",
        ),
        KBMatch(
            similarity=0.78, ticket_id="KB-007",
            title="库存扣减非原子操作引发数据不一致",
            root_cause="StockService.deduct 未使用 Lua 脚本保证原子性",
            fix_code="-- lua atomic deduct",
            microservice="stock-svc", error_code="INCONSIST_002",
        ),
    ]


@pytest.fixture
def chain():
    return [
        CallStackNode(
            symbol="sym:OrderLockService:acquire", file="OrderLockService.java",
            line=127, score=0.94, reason="sync_block+single_key_lock",
        ),
        CallStackNode(
            symbol="sym:StockService:deduct", file="StockService.java",
            line=88, score=0.81, reason="non_atomic_decrement",
        ),
    ]


# ============================================================================
# Red: _step_synthesize 应从 RAG 匹配结果合成上下文
# ============================================================================

class TestStepSynthesizeFromRag:
    """_step_synthesize 应从 kb_matches 合成 patch / historical / root_cause 上下文。"""

    def test_populates_synthesis_key(self, pipeline, req, kb_matches, chain):
        ctx = {"kb_matches": kb_matches, "chain": chain}
        pipeline._step_synthesize(req, ctx)
        assert "synthesis" in ctx, "_step_synthesize 应填充 ctx['synthesis']"

    def test_patch_from_top_match_fix_code(self, pipeline, req, kb_matches, chain):
        ctx = {"kb_matches": kb_matches, "chain": chain}
        pipeline._step_synthesize(req, ctx)
        synth = ctx["synthesis"]
        assert synth.get("patch_suggestion"), "应从 top match 的 fix_code 提取补丁建议"
        assert "seg" in synth["patch_suggestion"], "补丁应含分段锁逻辑"

    def test_historical_cases_from_ticket_ids(self, pipeline, req, kb_matches, chain):
        ctx = {"kb_matches": kb_matches, "chain": chain}
        pipeline._step_synthesize(req, ctx)
        synth = ctx["synthesis"]
        assert synth.get("historical_cases"), "应填充历史案例 ticket_id 列表"
        assert "KB-001" in synth["historical_cases"]
        assert "KB-007" in synth["historical_cases"]

    def test_root_cause_hint_from_top_match(self, pipeline, req, kb_matches, chain):
        ctx = {"kb_matches": kb_matches, "chain": chain}
        pipeline._step_synthesize(req, ctx)
        synth = ctx["synthesis"]
        assert synth.get("root_cause_hint"), "应从 top match 提取根因提示"
        assert "OrderLockService" in synth["root_cause_hint"]


# ============================================================================
# Red: _step_synthesize 应在无 RAG 匹配时优雅降级
# ============================================================================

class TestStepSynthesizeEmptyMatches:
    """_step_synthesize 应在 kb_matches 为空时使用调用链数据降级合成。"""

    def test_empty_matches_uses_chain(self, pipeline, req, chain):
        ctx = {"kb_matches": [], "chain": chain}
        pipeline._step_synthesize(req, ctx)
        synth = ctx["synthesis"]
        assert synth is not None, "无匹配时也应产出 synthesis 上下文"
        assert synth.get("root_cause_hint"), "应从调用链热点降级提取根因提示"

    def test_empty_chain_and_matches(self, pipeline, req):
        ctx = {"kb_matches": [], "chain": []}
        pipeline._step_synthesize(req, ctx)
        assert "synthesis" in ctx, "即使无数据也应产出空 synthesis 上下文"


# ============================================================================
# Red: _assemble 应消费 synthesis 上下文填充 Solution 字段
# ============================================================================

class TestAssembleConsumesSynthesis:
    """_assemble 应使用 ctx['synthesis'] 填充 Solution 的 patch_suggestion / historical_cases。"""

    def test_solution_patch_suggestion_populated(self, pipeline, req, kb_matches, chain):
        ctx = {"kb_matches": kb_matches, "chain": chain, "oc": {"degraded": True}}
        pipeline._step_synthesize(req, ctx)
        report: AnalysisReport = pipeline._assemble(req, ctx)
        assert report.solution.patch_suggestion, "Solution.patch_suggestion 应从 synthesis 填充"
        assert "seg" in report.solution.patch_suggestion

    def test_solution_historical_cases_populated(self, pipeline, req, kb_matches, chain):
        ctx = {"kb_matches": kb_matches, "chain": chain, "oc": {"degraded": True}}
        pipeline._step_synthesize(req, ctx)
        report = pipeline._assemble(req, ctx)
        assert report.solution.historical_cases, "Solution.historical_cases 应从 synthesis 填充"
        assert "KB-001" in report.solution.historical_cases

    def test_solution_best_practices_populated(self, pipeline, req, kb_matches, chain):
        ctx = {"kb_matches": kb_matches, "chain": chain, "oc": {"degraded": True}}
        pipeline._step_synthesize(req, ctx)
        report = pipeline._assemble(req, ctx)
        assert report.solution.best_practices, "Solution.best_practices 应被填充"
        assert len(report.solution.best_practices) > 0

    def test_solution_empty_synthesis_falls_back(self, pipeline, req):
        """无 synthesis 上下文时应降级为空字符串，不应报错。"""
        ctx = {"kb_matches": [], "chain": [], "oc": {"degraded": True}}
        report = pipeline._assemble(req, ctx)
        assert report.solution.patch_suggestion == ""
        assert report.solution.historical_cases == []
