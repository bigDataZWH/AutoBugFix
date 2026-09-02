"""Spec 2 CodeGraph REST API 测试套件。"""
from __future__ import annotations

import pytest


class TestCodeGraphRestApi:
    """CodeGraph REST API 端点测试"""

    @pytest.mark.asyncio
    async def test_node_not_found(self, asgi_client):
        resp = await asgi_client.get("/api/v1/codegraph/node/nonexistent_symbol_999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_callers_endpoint(self, asgi_client):
        resp = await asgi_client.get("/api/v1/codegraph/callers/nonexistent_symbol_999")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            assert "callers" in data

    @pytest.mark.asyncio
    async def test_callees_endpoint(self, asgi_client):
        resp = await asgi_client.get("/api/v1/codegraph/callees/nonexistent_symbol_999")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            assert "callees" in data

    @pytest.mark.asyncio
    async def test_explore_endpoint(self, asgi_client):
        resp = await asgi_client.get("/api/v1/codegraph/explore/nonexistent_symbol_999")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            assert "nodes" in data
            assert "edges" in data


class TestCodeGraphSchema:
    """CodeGraph schema 与存储验证"""

    def test_schema_initializable(self):
        from app.codegraph import CodeGraph
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        cg = CodeGraph(db_path=db_path)
        cg.init_schema()
        # 验证表存在
        import sqlite3
        conn = sqlite3.connect(db_path)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t[0] for t in tables]
        assert "nodes" in table_names
        assert "edges" in table_names
        conn.close()

    def test_node_upsert(self):
        from app.codegraph import CodeGraph
        from app.models import CPGNode
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        cg = CodeGraph(db_path=db_path)
        cg.init_schema()
        cg.upsert_node(CPGNode(
            symbol="test.fn", type="function", file="test.py", line=1,
            fan_in=2, fan_out=3, complexity=5
        ))
        # 查询验证
        with cg._conn() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE symbol = ?", ("test.fn",)).fetchone()
        assert row is not None
        assert row["symbol"] == "test.fn"
        assert row["fan_in"] == 2
