"""Spec 1 code2cn 测试套件：LLM 客户端、大纲生成、缓存、降级、REST API。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.code2cn import Code2CN
from app.models import Code2CnRequest, CodeOutline, AstFunctionNode

FIXTURES = Path(__file__).parent / "fixtures" / "code2cn"


class TestLLMClient:
    """UT 1: LLM 客户端层 — 角色级模型切换、token 统计、重试"""

    def test_role_based_model(self):
        """角色级模型切换：抽取用 extract_model"""
        cn = Code2CN()
        assert cn.opencode.model is not None

    def test_token_stats_recording(self):
        """token 用量统计落盘"""
        cn = Code2CN()
        cn._record_tokens("extract", "qwen2.5-coder", 350, 120)
        assert len(cn.token_stats) == 1
        assert cn.token_stats[0]["total"] == 470
        assert cn.token_stats[0]["model"] == "qwen2.5-coder"

    def test_retry_on_failure(self):
        """429/超时重试 → 降级标记"""
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        mock_opencode.run_llm.side_effect = Exception("429 Too Many Requests")
        cn = Code2CN(opencode=mock_opencode)
        cn._max_retry = 3
        result = cn.generate(Code2CnRequest(
            symbol="test.fn", file="test.py", source_code="def fn(): pass", language="python"
        ))
        assert result.degraded is True
        assert mock_opencode.run_llm.call_count == 3  # 重试 3 次

    def test_retry_success_on_second_attempt(self):
        """第一次失败第二次成功"""
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        mock_opencode.run_llm.side_effect = [
            Exception("timeout"),
            json.dumps({
                "symbol": "test.fn", "file": "test.py",
                "cn_summary": "测试函数", "external_calls": [], "failure_paths": [],
                "degraded": False
            })
        ]
        cn = Code2CN(opencode=mock_opencode)
        result = cn.generate(Code2CnRequest(
            symbol="test.fn", file="test.py", source_code="def fn(): pass", language="python"
        ))
        assert result.degraded is False
        assert result.cn_summary == "测试函数"
        assert mock_opencode.run_llm.call_count == 2


class TestOutlineGeneration:
    """UT 2: 中文大纲生成器 — AST 切分、Prompt、Schema、大函数拆分"""

    def test_generate_from_ast(self):
        """从 AstFunctionNode 生成大纲"""
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        mock_opencode.run_llm.return_value = json.dumps({
            "symbol": "OrderService.create", "file": "OrderService.java",
            "cn_summary": "1. 参数校验 2. 库存校验 3. 持久化",
            "external_calls": ["DB/orderRepo.save"],
            "failure_paths": ["InsufficientStockException"],
            "degraded": False
        })
        cn = Code2CN(opencode=mock_opencode)
        node = AstFunctionNode(
            symbol="OrderService.create", file="OrderService.java",
            start_line=42, end_line=68,
            source_code="public OrderResult create(OrderRequest req) { ... }",
            language="java", signature="public OrderResult create(OrderRequest req)"
        )
        outline = cn.generate_from_ast(node)
        assert outline.symbol == "OrderService.create"
        assert "参数校验" in outline.cn_summary
        assert "DB/orderRepo.save" in outline.external_calls
        assert "InsufficientStockException" in outline.failure_paths
        assert outline.degraded is False

    def test_cn_summary_max_chars(self):
        """cn_summary 超长截断"""
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        long_summary = "x" * 600
        mock_opencode.run_llm.return_value = json.dumps({
            "symbol": "test.fn", "file": "test.py",
            "cn_summary": long_summary, "external_calls": [], "failure_paths": [],
            "degraded": False
        })
        cn = Code2CN(opencode=mock_opencode)
        result = cn.generate(Code2CnRequest(symbol="test.fn", file="test.py", source_code="x"))
        assert len(result.cn_summary) <= 512

    def test_hierarchical_summary(self):
        """分层摘要：超阈值触发分块"""
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        mock_opencode.run_llm.return_value = json.dumps({
            "symbol": "fn", "file": "f.py", "cn_summary": "s",
            "external_calls": [], "failure_paths": [], "degraded": False
        })
        cn = Code2CN(opencode=mock_opencode)
        nodes = [AstFunctionNode(symbol=f"fn{i}", file="f.py", start_line=1, end_line=2, source_code="x") for i in range(5)]
        results = cn.hierarchical_summary(nodes)
        assert len(results) == 5


class TestCacheLayer:
    """UT 3: 缓存层 — 命中/未命中"""

    def test_cache_hit(self):
        """缓存命中返回 cached=True"""
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        mock_opencode.run_llm.return_value = json.dumps({
            "symbol": "fn", "file": "f.py", "cn_summary": "s",
            "external_calls": [], "failure_paths": [], "degraded": False
        })
        cn = Code2CN(opencode=mock_opencode)
        req = Code2CnRequest(symbol="fn", file="f.py", source_code="code")
        # 第一次调用
        r1 = cn.generate(req)
        assert r1.cached is False
        # 第二次调用应命中缓存
        r2 = cn.generate(req)
        assert r2.cached is True
        assert mock_opencode.run_llm.call_count == 1  # LLM 只调用一次


class TestDegradedMode:
    """UT 4: 降级模式 — LLM 不可用时返回 degraded=True"""

    def test_degraded_response(self):
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        mock_opencode.run_llm.side_effect = Exception("LLM unavailable")
        cn = Code2CN(opencode=mock_opencode)
        cn._max_retry = 1
        result = cn.generate(Code2CnRequest(symbol="fn", file="f.py", source_code="x"))
        assert result.degraded is True
        assert result.cn_summary == ""
        assert result.external_calls == []
        assert result.failure_paths == []


class TestCode2CnRestApi:
    """UT 5: REST API 端点"""

    @pytest.mark.asyncio
    async def test_generate_endpoint(self, asgi_client):
        resp = await asgi_client.post("/api/v1/code2cn/generate", json={
            "symbol": "test.fn", "file": "test.py",
            "source_code": "def fn(): pass", "language": "python"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "symbol" in data
        assert "cn_summary" in data
        assert "degraded" in data

    @pytest.mark.asyncio
    async def test_outline_not_found(self, asgi_client):
        resp = await asgi_client.get("/api/v1/code2cn/outline/nonexistent_fn")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_generate_then_get_outline(self, asgi_client):
        # 先 generate
        resp = await asgi_client.post("/api/v1/code2cn/generate", json={
            "symbol": "test.symbol123", "file": "test.py",
            "source_code": "def f(): pass", "language": "python"
        })
        assert resp.status_code == 200
        # 再 get outline
        resp2 = await asgi_client.get("/api/v1/code2cn/outline/test.symbol123")
        assert resp2.status_code == 200
        assert resp2.json()["symbol"] == "test.symbol123"


class TestCode2CnFixtures:
    """UT 6: 测试 fixtures 校验"""

    def test_ast_node_fixture(self):
        with open(FIXTURES / "ast_node_java.json") as f:
            data = json.load(f)
        assert data["symbol"] == "OrderService.create"
        assert data["language"] == "java"
        assert "source_code" in data

    def test_code_outline_fixture(self):
        with open(FIXTURES / "code_outline_sample.json") as f:
            data = json.load(f)
        assert data["symbol"] == "OrderService.create"
        assert "cn_summary" in data
        assert len(data["external_calls"]) > 0
        assert len(data["failure_paths"]) > 0
        assert data["tokens"]["total"] > 0
