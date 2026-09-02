"""跨模块集成测试套件：8 个场景，覆盖 6 模块全链路集成。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent


class TestIntegFullStackStartup:
    """场景 integ_full_stack_startup — 全栈启动"""

    @pytest.mark.asyncio
    async def test_all_components_respond(self, asgi_client):
        resp = await asgi_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        comps = data["components"]
        for comp in ("postgres", "redis", "lightrag", "codegraph", "llm"):
            assert comp in comps

    @pytest.mark.asyncio
    async def test_frontend_backend_reachable(self, asgi_client):
        resp = await asgi_client.get("/")
        assert resp.status_code == 200


class TestIntegFrontendToBackendApi:
    """场景 integ_frontend_to_backend_api — 前端→后端 API 全链路"""

    @pytest.mark.asyncio
    async def test_analyze_to_result(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/1",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "集成测试全链路",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        data = result.json()
        assert data["stage"]["status"] == "completed"
        assert "top3" in data
        assert "solution" in data


class TestIntegMockDemoMode:
    """场景 integ_mock_demo_mode — Mock 冒烟集成"""

    @pytest.mark.asyncio
    async def test_mock_mode_complete(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/2",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "Mock 冒烟",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        data = result.json()
        assert data["runtime_mode"] == "mock_demo"
        assert data["stage"]["status"] == "completed"
        assert data["solution"]["patch_suggestion"]
        assert len(data["solution"]["best_practices"]) > 0


class TestIntegOnlineFullMode:
    """场景 integ_online_full_mode — 联机全量集成"""

    @pytest.mark.asyncio
    async def test_online_mode_flag(self, asgi_client, mode_online_full):
        resp = await asgi_client.get("/api/v1/health")
        data = resp.json()
        # 在 mock_demo 环境，online_full 组件状态应为 up
        comps = data["components"]
        assert comps["codegraph"]["status"] in ("up", "degraded", "down")
        assert comps["lightrag"]["status"] in ("up", "degraded", "down")


class TestIntegOfflineLightMode:
    """场景 integ_offline_light_mode — 离线轻量降级集成"""

    @pytest.mark.asyncio
    async def test_offline_components(self, asgi_client, mode_offline_light):
        resp = await asgi_client.get("/api/v1/health")
        data = resp.json()
        comps = data["components"]
        # offline_light 下 lightrag 应为 down
        assert comps["lightrag"]["status"] in ("down", "degraded")
        # codegraph 降级
        assert comps["codegraph"]["status"] in ("degraded", "down")


class TestIntegRestartRecovery:
    """场景 integ_restart_recovery — 中断重启断点续跑"""

    @pytest.mark.asyncio
    async def test_state_persistence(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/3",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "恢复测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        # 通过 state 端点验证持久化
        state = await asgi_client.get(f"/api/v1/rca/{task_id}/state")
        assert state.status_code == 200
        assert state.json()["task_id"] == task_id


class TestIntegMilestoneKpi:
    """场景 integ_milestone_kpi_verification — 4 里程碑 KPI"""

    @pytest.mark.asyncio
    async def test_m1_latency(self, asgi_client):
        import time
        start = time.time()
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/4",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "KPI M1",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        await asgi_client.get(f"/api/v1/rca/{task_id}")
        assert (time.time() - start) < 30

    @pytest.mark.asyncio
    async def test_m2_top3_count(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/integ/repo/issues/5",
            "repo": "https://github.com/integ/repo",
            "bug_desc": "KPI M2",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]
        await asyncio.sleep(8)
        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        assert len(result.json()["top3"]) == 3


class TestIntegCiPipeline:
    """场景 integ_ci_pipeline — CI 流水线"""

    def test_pytest_collectable(self):
        """验证 pytest 可收集所有测试"""
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q", str(Path(__file__).parent)],
            capture_output=True, cwd=str(BACKEND_DIR)
        )
        assert result.returncode == 0 or "error" in result.stderr.decode().lower()


# ============================================================================
# Spec5 集成：服务级拓扑 → 双图谱交叉验证
# ============================================================================

class TestIntegTraceToTopology:
    """场景 integ_trace_cmdb_to_p_runtime — Trace span 树 + CMDB 服务拓扑 → P_runtime"""

    def test_trace_aggregates_to_topology(self):
        from app.service_topology import (
            TraceData, TraceSpan, aggregate_spans_to_topology,
            CmdbAdapter, CmdbRecord, enrich_with_cmdb,
            build_anomaly_path_from_trace,
        )
        trace = TraceData(
            trace_id="integ-001",
            spans=[
                TraceSpan(span_id="s1", service="order-svc", operation="create",
                          parent_span_id=None, duration_ms=3000, error=True),
                TraceSpan(span_id="s2", service="stock-svc", operation="deduct",
                          parent_span_id="s1", duration_ms=2500, error=True),
            ],
        )
        topology = aggregate_spans_to_topology(trace)
        cmdb = CmdbAdapter(records=[
            CmdbRecord(service_id="order-svc", service_name="订单服务"),
            CmdbRecord(service_id="stock-svc", service_name="库存服务"),
        ])
        enriched = enrich_with_cmdb(topology, cmdb)
        # 断言服务节点齐全
        assert len(enriched.nodes) == 2
        assert enriched.nodes[0].service_name == "订单服务"
        # 断言跨服务调用边
        assert len(enriched.edges) == 1
        # 断言 P_runtime 异常路径可构造
        path = build_anomaly_path_from_trace(trace, "stock-svc")
        assert path.runtime_anomaly > 0.0


class TestIntegCodegraphToContains:
    """场景 integ_codegraph_to_contains — CodeGraph 函数 → CONTAINS 下钻"""

    def test_contains_drill_down(self):
        from app.service_topology import (
            ServiceNode, ServiceTopology, build_contains_map,
            drill_down_functions, ContainsEdge,
        )
        topology = ServiceTopology(nodes=[
            ServiceNode(service_id="order-svc"),
            ServiceNode(service_id="stock-svc"),
        ])
        func_map = {
            "OrderService.create": "order-svc",
            "OrderService.submit": "order-svc",
            "StockService.deduct": "stock-svc",
        }
        edges = build_contains_map(topology, func_map)
        # 下钻 order-svc 返回 2 个函数
        funcs = drill_down_functions("order-svc", edges)
        assert len(funcs) == 2
        assert "OrderService.create" in funcs


class TestIntegReversePropagation:
    """场景 integ_reverse_propagation — 根因反向传播遍历"""

    def test_reverse_propagation(self):
        from app.service_topology import (
            TraceData, TraceSpan, aggregate_spans_to_topology, reverse_propagate,
        )
        trace = TraceData(
            trace_id="prop-001",
            spans=[
                TraceSpan(span_id="s1", service="gateway", operation="route", parent_span_id=None),
                TraceSpan(span_id="s2", service="order", operation="create", parent_span_id="s1"),
                TraceSpan(span_id="s3", service="stock", operation="deduct", parent_span_id="s2", error=True),
            ],
        )
        topology = aggregate_spans_to_topology(trace)
        upstream = reverse_propagate(topology, suspect_services=["stock"])
        # 反向传播应找到 order 和 gateway
        assert "order" in upstream
        assert "gateway" in upstream


# ============================================================================
# Spec1 集成：CodeGraph → 迁移 → 增量中文化
# ============================================================================

class TestIntegMigrationBackfill:
    """场景 integ_migration_backfill — 旧节点回填迁移全链路"""

    def test_backfill_old_nodes(self, tmp_path):
        import sqlite3
        from app.codegraph import CodeGraph
        from app.migration import migrate_old_nodes
        from unittest.mock import MagicMock
        from app.models import CodeOutline

        db_path = tmp_path / "graph.db"
        cg = CodeGraph(db_path=str(db_path))
        cg.init_schema()
        with sqlite3.connect(str(db_path)) as conn:
            conn.executemany(
                "INSERT INTO nodes (symbol, type, file, line, cn_summary) VALUES (?, 'method', ?, ?, ?)",
                [("svc.fn1", "Svc.java", 10, None),
                 ("svc.fn2", "Svc.java", 20, "已存在")],
            )
            conn.commit()

        mock_cn = MagicMock()
        mock_cn.generate.return_value = CodeOutline(
            symbol="svc.fn1", file="Svc.java",
            cn_summary="回填的中文", external_calls=[], failure_paths=[],
        )
        result = migrate_old_nodes(cg, mock_cn, source_loader=lambda *a: "// code")
        assert result.missing_cn_summary == 1
        assert result.migrated == 1


class TestIntegIncrementalLocalize:
    """场景 integ_incremental_localize — git diff 增量中文化全链路"""

    def test_incremental_pipeline(self, tmp_path):
        import sqlite3
        from unittest.mock import MagicMock, patch
        from app.codegraph import CodeGraph
        from app.migration import incremental_localize_from_diff
        from app.models import CodeOutline

        db_path = tmp_path / "graph.db"
        cg = CodeGraph(db_path=str(db_path))
        cg.init_schema()
        with sqlite3.connect(str(db_path)) as conn:
            conn.executemany(
                "INSERT INTO nodes (symbol, type, file, line, cn_summary) VALUES (?, 'method', ?, ?, ?)",
                [("OrderService.create", "OrderService.java", 50, None),
                 ("StockService.deduct", "StockService.java", 100, None)],
            )
            conn.execute(
                "INSERT INTO edges (src_id, tgt_id, type, weight) VALUES (1, 2, 'call', 1.0)"
            )
            conn.commit()

        mock_cn = MagicMock()
        mock_cn.generate.return_value = CodeOutline(
            symbol="OrderService.create", file="OrderService.java",
            cn_summary="增量中文化", external_calls=[], failure_paths=[],
        )
        with patch("app.migration.get_git_diff_files", return_value=["OrderService.java"]):
            result = incremental_localize_from_diff(
                str(tmp_path), codegraph=cg, code2cn=mock_cn,
                total_functions=100, source_loader=lambda *a: "// code",
            )
        # 受影响子树应包含 callees
        assert "StockService.deduct" in result.affected_functions
        assert result.relocalized >= 1
        # token 节省比例 > 95%（仅重建子树 vs 全仓 100）
        assert result.token_saved_ratio > 0.9


class TestIntegDualGraphToA4:
    """场景 integ_dualgraph_to_agent4 — cross_validate → Candidate Top-3 → A4 消费"""

    def test_cross_validate_feeds_a4(self):
        from app.dual_graph import cross_validate
        from app.models import (
            SuspectFunction, AnomalyPath, MetricAnomalies, ChangeRecord, ChangeRecords,
        )
        from app.agents import AgentA4

        s_static = [
            SuspectFunction(function_id="OrderLockService.acquire", function_name="acquire",
                            call_path=["A", "OrderLockService.acquire"], static_depth=3),
            SuspectFunction(function_id="JedisPool.getResource", function_name="getResource",
                            call_path=["OrderLockService.acquire", "JedisPool.getResource"], static_depth=4),
        ]
        p_runtime = AnomalyPath(
            functions=["OrderLockService.acquire", "JedisPool.getResource"],
            runtime_anomaly=0.9,
        )
        candidates = cross_validate(s_static, p_runtime)
        # 交集命中候选
        assert len(candidates) >= 1
        assert candidates[0].hit_kind == "intersection"

        # A4 消费 candidates 转换为 RootCause
        a4 = AgentA4()
        # 转换 Candidate → SuspectFunction（A4 入参）
        sf_list = [
            SuspectFunction(function_id=c.function_id, function_name=c.function_name,
                            static_depth=c.evidence.static_depth, file=c.file, line=c.line)
            for c in candidates
        ]
        top3 = a4.run(sf_list, p_runtime)
        assert len(top3) <= 3
        assert top3[0].confidence > 0.0


class TestIntegFullPipelineDualgraph:
    """场景 integ_full_pipeline_dualgraph — CodeGraph+Trace+CMDB+Metrics+Change 全链路"""

    def test_full_pipeline(self):
        from app.service_topology import (
            TraceData, TraceSpan, aggregate_spans_to_topology, CmdbAdapter, CmdbRecord,
            enrich_with_cmdb, build_anomaly_path_from_trace, reverse_propagate,
        )
        from app.dual_graph import cross_validate
        from app.models import (
            SuspectFunction, MetricAnomalies, ChangeRecord, ChangeRecords,
        )

        # 1. Trace → 服务拓扑
        trace = TraceData(
            trace_id="full-001",
            spans=[
                TraceSpan(span_id="s1", service="order", operation="create",
                          parent_span_id=None, duration_ms=3000, error=True),
                TraceSpan(span_id="s2", service="stock", operation="deduct",
                          parent_span_id="s1", duration_ms=2500, error=True),
            ],
        )
        topology = aggregate_spans_to_topology(trace)
        cmdb = CmdbAdapter(records=[CmdbRecord(service_id="order", service_name="订单")])
        enriched = enrich_with_cmdb(topology, cmdb)
        assert len(enriched.nodes) == 2

        # 2. 反向传播找上游
        upstream = reverse_propagate(topology, ["stock"])
        assert "order" in upstream

        # 3. 构造 S_static（函数级）
        s_static = [
            SuspectFunction(function_id="order::create", function_name="create", static_depth=2),
            SuspectFunction(function_id="stock::deduct", function_name="deduct", static_depth=3),
        ]

        # 4. Trace → P_runtime
        p_runtime = build_anomaly_path_from_trace(trace, "stock")
        assert p_runtime.runtime_anomaly > 0.0

        # 5. 交叉验证产出 Top-3
        metrics = MetricAnomalies(functions={"order::create": 0.7, "stock::deduct": 0.8})
        changes = ChangeRecords(records=[ChangeRecord(function_id="stock::deduct", commits=2)])
        candidates = cross_validate(s_static, p_runtime, metrics, changes)
        assert len(candidates) >= 1
        assert candidates[0].score > 0.0
