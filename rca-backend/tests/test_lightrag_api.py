"""Spec 3 LightRAG 测试套件：三路检索路由、意图分类、REST API、降级。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lightrag_adapter import LightRAGAdapter, intent_to_mode, lightrag

FIXTURES = Path(__file__).parent / "fixtures" / "lightrag"


class TestIntentClassification:
    """UT: 三路检索意图分类"""

    def test_history_intent(self):
        assert lightrag.classify_intent("有没有类似的历史问题单") == "history"

    def test_history_keyword(self):
        assert lightrag.classify_intent("之前遇到过什么相似问题") == "history"

    def test_propagation_intent(self):
        assert lightrag.classify_intent("这个函数的调用链是什么") == "propagation"

    def test_propagation_keyword(self):
        assert lightrag.classify_intent("上游谁调用了这个函数") == "propagation"

    def test_architecture_intent(self):
        assert lightrag.classify_intent("整体模块架构是怎样的") == "architecture"

    def test_architecture_keyword(self):
        assert lightrag.classify_intent("全局设计结构概览") == "architecture"

    def test_default_to_history(self):
        assert lightrag.classify_intent("订单创建超时") == "history"

    def test_priority_propagation_over_history(self):
        """传播关键词优先于历史关键词"""
        assert lightrag.classify_intent("历史调用链追溯") == "propagation"


class TestIntentToMode:
    """UT: intent → mode 映射"""

    def test_history_to_hybrid(self):
        assert intent_to_mode("history") == "hybrid"

    def test_propagation_to_hybrid(self):
        assert intent_to_mode("propagation") == "hybrid"

    def test_architecture_to_high_level(self):
        assert intent_to_mode("architecture") == "high_level"

    def test_low_level_passthrough(self):
        assert intent_to_mode("low_level") == "low_level"

    def test_unknown_defaults_hybrid(self):
        assert intent_to_mode("unknown_intent") == "hybrid"


class TestLightRAGDegraded:
    """UT: LightRAG 不可用时降级"""

    def test_unavailable_aquery(self):
        adapter = LightRAGAdapter()
        if not adapter.available:
            import asyncio
            result = asyncio.run(adapter.aquery("test", mode="hybrid"))
            assert result.degraded is True
            assert "unavailable" in result.route

    def test_unavailable_ainsert(self):
        adapter = LightRAGAdapter()
        if not adapter.available:
            import asyncio
            ok = asyncio.run(adapter.ainsert("test"))
            assert ok is False

    def test_unavailable_ainsert_custom_kg(self):
        from app.models import AstKg, AstKgEntity
        adapter = LightRAGAdapter()
        if not adapter.available:
            import asyncio
            kg = AstKg(entities=[AstKgEntity(entity_name="func:test", description="测试")])
            ok = asyncio.run(adapter.ainsert_custom_kg(kg))
            assert ok is False


class TestRagRestApi:
    """UT: LightRAG REST API 端点"""

    @pytest.mark.asyncio
    async def test_query_endpoint(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rag/query", params={
            "query": "订单创建超时", "intent": "history", "top_k": 5
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert "route" in data
        assert "degraded" in data

    @pytest.mark.asyncio
    async def test_query_architecture_intent(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rag/query", params={
            "query": "全局架构概览", "intent": "architecture"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "high_level"

    @pytest.mark.asyncio
    async def test_query_propagation_intent(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rag/query", params={
            "query": "调用链追溯", "intent": "propagation"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_insert_endpoint(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rag/insert", params={"text": "测试文档"})
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert "degraded" in data

    @pytest.mark.asyncio
    async def test_insert_kg_endpoint(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rag/insert_kg", json={
            "entities": [
                {"entity_name": "func:test", "type": "function", "description": "测试函数"}
            ],
            "relationships": []
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data


class TestLightragFixtures:
    """UT: 测试 fixtures 校验"""

    def test_ast_kg_fixture(self):
        with open(FIXTURES / "ast_kg_sample.json") as f:
            data = json.load(f)
        assert len(data["entities"]) == 3
        assert data["entities"][0]["entity_name"].startswith("func:")
        assert len(data["relationships"]) == 2
        assert data["relationships"][0]["weight"] == 8.0

    def test_llm_response_fixture(self):
        with open(FIXTURES / "llm_response_sample.json") as f:
            data = json.load(f)
        assert "history" in data
        assert "propagation" in data
        assert "architecture" in data
        assert "OrderService" in data["history"]
        assert "JedisPool" in data["propagation"]
        assert "订单中心" in data["architecture"]


class TestFullPipelineIntegration:
    """集成测试: code2cn → lightrag 注入 → 检索全链路"""

    @pytest.mark.asyncio
    async def test_code2cn_to_rag_insert(self, asgi_client):
        """生成大纲 → 注入 LightRAG → 查询"""
        # Step 1: 生成中文大纲
        resp1 = await asgi_client.post("/api/v1/code2cn/generate", json={
            "symbol": "test.PipelineIntegration", "file": "test.py",
            "source_code": "def f(): pass", "language": "python"
        })
        assert resp1.status_code == 200
        outline = resp1.json()

        # Step 2: 注入到 LightRAG (如果不可用会返回 degraded)
        resp2 = await asgi_client.post("/api/v1/rag/insert_kg", json={
            "entities": [
                {"entity_name": f"func:{outline['symbol']}", "type": "function",
                 "description": outline.get("cn_summary", "test")}
            ],
            "relationships": []
        })
        assert resp2.status_code == 200
        assert "success" in resp2.json()

        # Step 3: 查询
        resp3 = await asgi_client.post("/api/v1/rag/query", params={
            "query": "test function", "intent": "history", "top_k": 3
        })
        assert resp3.status_code == 200
        assert "route" in resp3.json()
