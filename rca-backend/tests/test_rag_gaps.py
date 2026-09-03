"""P1-G: RAG 测试缺口（G9 云捷双写、G10 空查询）。

- G9: POST /api/v1/yunjie/import — 验证云捷导入执行 ChromaDB + LightRAG 双写
- G10: Retriever.hybrid_search 空查询/空白查询优雅降级
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.main import retriever


# ============================================================================
# G9: 云捷双写 — POST /api/v1/yunjie/import
# ============================================================================

_YUNJIE_TICKETS = [
    {
        "ticket_id": "YUNJIE-TEST-G9-001",
        "title": "库存超卖 G9测试",
        "description": "高并发场景下库存扣减非原子",
        "root_cause": "StockService.deduct 未使用 Lua 原子扣减",
        "fix_code": "redis.eval(lua_atomic_deduct)",
        "microservice": "stock-svc",
        "module": "stock",
        "error_code": "OVERSELL_G9",
        "severity": "P0",
    },
    {
        "ticket_id": "YUNJIE-TEST-G9-002",
        "title": "订单重复入账 G9测试",
        "description": "重试回调导致重复入账",
        "root_cause": "OrderService.create 缺少幂等键",
        "fix_code": "redis.setnx(idempotent_key)",
        "microservice": "order-svc",
        "module": "order",
        "error_code": "DUP_G9",
        "severity": "P1",
    },
]


class TestG9YunjieDualWrite:
    """G9: 云捷导入应同时写入 ChromaDB 和 LightRAG（双写）。"""

    @pytest.mark.asyncio
    async def test_dual_write_to_chroma_and_lightrag(self, asgi_client):
        before_count = retriever.count()
        ainsert_mock = AsyncMock(return_value=True)

        with patch("app.main.opencode.fetch_yunjie_tickets",
                    return_value=_YUNJIE_TICKETS), \
             patch("app.main.lightrag.ainsert", ainsert_mock):
            resp = await asgi_client.post("/api/v1/yunjie/import", json={
                "ticket_refs": ["YUNJIE-TEST-G9-001", "YUNJIE-TEST-G9-002"],
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert retriever.count() == before_count + 2
        assert ainsert_mock.await_count == 2, (
            "LightRAG ainsert 应被调用 2 次（每张问题单一次）"
        )
        first_call_text = ainsert_mock.call_args_list[0].args[0]
        assert "YUNJIE-TEST-G9-001" in first_call_text
        second_call_args = ainsert_mock.call_args_list[1]
        assert second_call_args.kwargs.get("ids") == "yunjie:YUNJIE-TEST-G9-002"

        retriever.delete_tickets(["YUNJIE-TEST-G9-001", "YUNJIE-TEST-G9-002"])

    @pytest.mark.asyncio
    async def test_lightrag_available_flag_is_false(self, asgi_client):
        ainsert_mock = AsyncMock(return_value=True)

        with patch("app.main.opencode.fetch_yunjie_tickets",
                    return_value=[_YUNJIE_TICKETS[0]]), \
             patch("app.main.lightrag.ainsert", ainsert_mock), \
             patch("app.main.lightrag._available", new=True):
            resp = await asgi_client.post("/api/v1/yunjie/import", json={
                "ticket_refs": ["YUNJIE-TEST-G9-001"],
            })

        data = resp.json()
        assert "lightrag_degraded" in data
        assert data["lightrag_degraded"] is False

        retriever.delete_tickets(["YUNJIE-TEST-G9-001"])

    @pytest.mark.asyncio
    async def test_lightrag_unavailable_flag_is_true(self, asgi_client):
        ainsert_mock = AsyncMock(return_value=False)

        with patch("app.main.opencode.fetch_yunjie_tickets",
                    return_value=[_YUNJIE_TICKETS[0]]), \
             patch("app.main.lightrag.ainsert", ainsert_mock), \
             patch("app.main.lightrag._available", new=False):
            resp = await asgi_client.post("/api/v1/yunjie/import", json={
                "ticket_refs": ["YUNJIE-TEST-G9-001"],
            })

        data = resp.json()
        assert data["lightrag_degraded"] is True

        retriever.delete_tickets(["YUNJIE-TEST-G9-001"])

    @pytest.mark.asyncio
    async def test_empty_ticket_refs_imports_zero(self, asgi_client):
        ainsert_mock = AsyncMock(return_value=True)

        with patch("app.main.opencode.fetch_yunjie_tickets",
                    return_value=[]), \
             patch("app.main.lightrag.ainsert", ainsert_mock):
            resp = await asgi_client.post("/api/v1/yunjie/import", json={
                "ticket_refs": [],
            })

        assert resp.status_code == 200
        assert resp.json()["imported"] == 0
        assert ainsert_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_lightrag_failure_does_not_block_chroma_write(self, asgi_client):
        before_count = retriever.count()
        ainsert_mock = AsyncMock(side_effect=RuntimeError("lightrag down"))

        with patch("app.main.opencode.fetch_yunjie_tickets",
                    return_value=[_YUNJIE_TICKETS[0]]), \
             patch("app.main.lightrag.ainsert", ainsert_mock):
            resp = await asgi_client.post("/api/v1/yunjie/import", json={
                "ticket_refs": ["YUNJIE-TEST-G9-001"],
            })

        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        assert retriever.count() == before_count + 1

        retriever.delete_tickets(["YUNJIE-TEST-G9-001"])


# ============================================================================
# G10: 空查询 — Retriever.hybrid_search 优雅降级
# ============================================================================

class TestG10EmptyQuery:
    """G10: 空查询/空白查询不应报错，应返回空结果。"""

    def test_empty_string_returns_empty_list(self):
        results = retriever.hybrid_search("")
        assert results == [], "空字符串查询应返回空列表"

    def test_whitespace_only_returns_empty_list(self):
        results = retriever.hybrid_search("   \t\n  ")
        assert results == [], "纯空白查询应返回空列表"

    def test_valid_query_returns_results(self):
        results = retriever.hybrid_search("超卖", top_k=3)
        assert isinstance(results, list)
        assert len(results) > 0, "KB 已 seed，有效查询应返回结果"
        assert results[0].ticket_id != ""

    @pytest.mark.asyncio
    async def test_kb_tickets_endpoint_empty_q_returns_all(self, asgi_client):
        resp = await asgi_client.get("/api/kb/tickets", params={"q": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] > 0, "空 q 应返回全部问题单"

    @pytest.mark.asyncio
    async def test_kb_tickets_endpoint_whitespace_q_returns_all(self, asgi_client):
        resp = await asgi_client.get("/api/kb/tickets", params={"q": "   "})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0, "纯空白 q 应返回全部问题单"
