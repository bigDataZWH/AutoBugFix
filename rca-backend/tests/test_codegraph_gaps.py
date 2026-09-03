"""P1-H: CodeGraph 测试缺口（H7 污点传播、H9 FTS5 全文搜索）。"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.codegraph import CodeGraph
from app.models import CPGNode


def _fresh_graph() -> tuple[CodeGraph, str]:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    Path(db_path).unlink()
    cg = CodeGraph(db_path=db_path)
    cg.init_schema()
    cg.upsert_node(CPGNode(
        symbol="svc.UserService:login", type="method", file="user.py", line=10,
        fan_in=1, fan_out=2, complexity=3, cn_summary="用户登录鉴权入口",
    ))
    cg.upsert_node(CPGNode(
        symbol="svc.TokenService:generate", type="method", file="token.py", line=20,
        fan_in=1, fan_out=1, complexity=2, cn_summary="生成访问令牌",
    ))
    cg.upsert_node(CPGNode(
        symbol="db.UserDao:save", type="method", file="dao.py", line=30,
        fan_in=1, fan_out=0, complexity=1, cn_summary="用户数据持久化",
    ))
    cg.upsert_node(CPGNode(
        symbol="svc.AuditService:record", type="method", file="audit.py", line=40,
        fan_in=1, fan_out=0, complexity=1, cn_summary="审计日志记录",
    ))
    cg.upsert_edge("svc.UserService:login", "svc.TokenService:generate")
    cg.upsert_edge("svc.UserService:login", "db.UserDao:save")
    cg.upsert_edge("svc.TokenService:generate", "svc.AuditService:record")
    return cg, db_path


# ============================================================================
# H7: 污点传播 — taint(entry, sink) DFS 路径搜索
# ============================================================================

class TestH7TaintMethod:
    """H7: CodeGraph.taint 污点传播路径搜索。"""

    def test_reachable_path_found(self):
        cg, _ = _fresh_graph()
        resp = cg.taint("svc.UserService:login", "db.UserDao:save")
        assert resp.entry_found is True
        assert resp.sink_found is True
        assert len(resp.paths) >= 1
        first = resp.paths[0]
        assert first["reachable"] is True
        hops = first["hops"]
        assert hops[0]["symbol"] == "svc.UserService:login"
        assert hops[-1]["symbol"] == "db.UserDao:save"
        assert all("file" in h and "line" in h for h in hops)

    def test_multi_hop_path_found(self):
        cg, _ = _fresh_graph()
        resp = cg.taint("svc.UserService:login", "svc.AuditService:record")
        assert resp.entry_found is True
        assert resp.sink_found is True
        assert len(resp.paths) >= 1
        hops = resp.paths[0]["hops"]
        assert hops[0]["symbol"] == "svc.UserService:login"
        assert hops[-1]["symbol"] == "svc.AuditService:record"
        assert len(hops) >= 3

    def test_reverse_direction_not_reachable(self):
        cg, _ = _fresh_graph()
        resp = cg.taint("db.UserDao:save", "svc.UserService:login")
        assert resp.entry_found is True
        assert resp.sink_found is True
        assert resp.paths == [], "反向不应可达（调用边方向相反）"

    def test_nonexistent_entry(self):
        cg, _ = _fresh_graph()
        resp = cg.taint("nonexistent.Symbol", "db.UserDao:save")
        assert resp.entry_found is False
        assert resp.paths == []

    def test_nonexistent_sink(self):
        cg, _ = _fresh_graph()
        resp = cg.taint("svc.UserService:login", "nonexistent.Sink")
        assert resp.sink_found is False
        assert resp.paths == []

    def test_entry_equals_sink(self):
        cg, _ = _fresh_graph()
        resp = cg.taint("svc.UserService:login", "svc.UserService:login")
        assert resp.entry_found is True
        assert resp.sink_found is True
        assert len(resp.paths) >= 1
        assert resp.paths[0]["hops"][0]["symbol"] == "svc.UserService:login"


# ============================================================================
# H9: FTS5 全文搜索 — search(query) 符号/中文摘要检索
# ============================================================================

class TestH9FtsSearch:
    """H9: CodeGraph.search FTS5 全文搜索。"""

    def test_symbol_keyword_match(self):
        cg, _ = _fresh_graph()
        results = cg.search("UserService")
        assert len(results) >= 1
        assert any(r.symbol == "svc.UserService:login" for r in results)

    def test_chinese_summary_match(self):
        cg, _ = _fresh_graph()
        results = cg.search("令牌")
        assert len(results) >= 1
        assert any(r.symbol == "svc.TokenService:generate" for r in results)

    def test_nonexistent_keyword_returns_empty(self):
        cg, _ = _fresh_graph()
        results = cg.search("NoSuchThingHere")
        assert results == []

    def test_empty_query_returns_empty(self):
        cg, _ = _fresh_graph()
        results = cg.search("")
        assert results == [], "空查询应优雅返回空列表而非抛异常"

    def test_whitespace_query_returns_empty(self):
        cg, _ = _fresh_graph()
        results = cg.search("   ")
        assert results == [], "纯空白查询应优雅返回空列表"

    def test_limit_parameter(self):
        cg, _ = _fresh_graph()
        results = cg.search("svc", limit=2)
        assert len(results) <= 2


# ============================================================================
# H7/H9 REST 端点级测试（全局 seed 实例）
# ============================================================================

class TestH7H9RestEndpoints:
    """H7/H9: 污点分析与全文搜索 REST 端点。"""

    @pytest.mark.asyncio
    async def test_taint_endpoint_reachable(self, asgi_client):
        resp = await asgi_client.get(
            "/api/v1/codegraph/taint",
            params={"entry": "sym:OrderController:createOrder",
                    "sink": "sym:StockService:deduct"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry_found"] is True
        assert data["sink_found"] is True
        assert len(data["paths"]) >= 1
        assert data["paths"][0]["reachable"] is True
        hops = data["paths"][0]["hops"]
        assert hops[0]["symbol"] == "sym:OrderController:createOrder"
        assert hops[-1]["symbol"] == "sym:StockService:deduct"

    @pytest.mark.asyncio
    async def test_taint_endpoint_nonexistent_entry(self, asgi_client):
        resp = await asgi_client.get(
            "/api/v1/codegraph/taint",
            params={"entry": "sym:Nonexistent:X", "sink": "sym:StockService:deduct"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry_found"] is False
        assert data["paths"] == []

    @pytest.mark.asyncio
    async def test_search_endpoint_symbol_match(self, asgi_client):
        resp = await asgi_client.get(
            "/api/v1/codegraph/search", params={"q": "OrderService"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any("OrderService" in n["symbol"] for n in data)

    @pytest.mark.asyncio
    async def test_search_endpoint_empty_query(self, asgi_client):
        resp = await asgi_client.get(
            "/api/v1/codegraph/search", params={"q": ""},
        )
        assert resp.status_code == 200
        assert resp.json() == []
