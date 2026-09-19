"""全面 E2E 测试套件：覆盖所有 API 端点与功能路径。

测试覆盖范围：
  - V2 端点: /api/analyze (POST+stream+get), /api/kb/* (import/count/list/delete), /api/v1/yunjie/import
  - V3 端点: /api/v1/rca/{id}/stream (SSE), /api/v1/rca/tasks, /api/v1/rca/{id}/confirm, /api/v1/rca/{id}/resume
  - 错误场景: 400 (缺少必填字段), 404 (任务不存在), 409 (分析未完成)
  - CodeGraph: node/callers/callees/explore 正常路径 + 404
  - code2cn: generate + outline (200 + 404)
  - LightRAG: query/insert/insert_kg (降级模式)
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
import httpx


# ============================================================================
# V2 端点: /api/analyze 全流程
# ============================================================================

class TestV2AnalyzeFlow:
    """V2 /api/analyze 提交 + SSE 流 + 获取报告。"""

    @pytest.mark.asyncio
    async def test_v2_analyze_returns_task_id(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "bug_link": "https://yunjie.example.com/t/INC-2025-018832",
            "bug_desc": "下单高峰期订单超卖",
            "repo": "https://github.com/test/repo",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["task_id"].startswith("v2-")

    @pytest.mark.asyncio
    async def test_v2_analyze_missing_fields_400(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "repo": "https://github.com/test/repo",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_v2_analyze_stream_and_report(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "bug_link": "https://yunjie.example.com/t/INC-2025-018832",
            "bug_desc": "下单高峰期订单超卖 库存为负 order-center",
            "repo": "https://github.com/test/repo",
        })
        task_id = resp.json()["task_id"]

        events = []
        async with asgi_client.stream("GET", f"/api/analyze/{task_id}/stream") as sse:
            async for line in sse.aiter_lines():
                if line.startswith("data: "):
                    evt = json.loads(line[6:])
                    events.append(evt)
                    if evt.get("type") in ("report", "error"):
                        break

        assert len(events) > 0
        report_evt = [e for e in events if e["type"] == "report"]
        assert len(report_evt) == 1
        report = report_evt[0]["data"]
        assert "rca" in report
        assert "solution" in report
        assert report["confidence"] > 0

    @pytest.mark.asyncio
    async def test_v2_get_report_404(self, asgi_client):
        resp = await asgi_client.get("/api/analyze/nonexistent-task")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_v2_get_report_409_not_finished(self, asgi_client):
        resp = await asgi_client.post("/api/analyze", json={
            "bug_link": "https://yunjie.example.com/t/INC-2025-018832",
            "bug_desc": "test 409",
            "repo": "https://github.com/test/repo",
        })
        task_id = resp.json()["task_id"]
        resp2 = await asgi_client.get(f"/api/analyze/{task_id}")
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_v2_stream_404(self, asgi_client):
        resp = await asgi_client.get("/api/analyze/nonexistent/stream")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_v2_report_has_kb_matches(self, asgi_client):
        """验证 V2 报告包含 KB 匹配结果（暴露 pipeline 字段映射缺陷）。"""
        resp = await asgi_client.post("/api/analyze", json={
            "bug_link": "https://yunjie.example.com/t/INC-2025-018832",
            "bug_desc": "下单高峰期订单超卖 库存为负 order-center OrderLockService",
            "repo": "https://github.com/test/repo",
        })
        task_id = resp.json()["task_id"]

        report = None
        async with asgi_client.stream("GET", f"/api/analyze/{task_id}/stream") as sse:
            async for line in sse.aiter_lines():
                if line.startswith("data: "):
                    evt = json.loads(line[6:])
                    if evt.get("type") == "report":
                        report = evt["data"]
                        break
                    if evt.get("type") == "error":
                        break

        assert report is not None, "未收到 report 事件"
        assert len(report.get("kb_matches", [])) > 0, "KB 匹配结果为空，pipeline 步骤可能未正确执行"


# ============================================================================
# V2 端点: 知识库管理
# ============================================================================

class TestV2KBManagement:
    """V2 /api/kb/import, /api/kb/count, /api/kb/tickets, /api/kb/tickets (DELETE)。"""

    @pytest.mark.asyncio
    async def test_kb_count(self, asgi_client):
        resp = await asgi_client.get("/api/kb/count")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert data["count"] > 0

    @pytest.mark.asyncio
    async def test_kb_list_tickets(self, asgi_client):
        resp = await asgi_client.get("/api/kb/tickets?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "kb_count" in data
        assert len(data["items"]) <= 5

    @pytest.mark.asyncio
    async def test_kb_list_tickets_with_query(self, asgi_client):
        resp = await asgi_client.get("/api/kb/tickets?q=超卖&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    @pytest.mark.asyncio
    async def test_kb_import_and_delete(self, asgi_client):
        test_id = f"TEST-E2E-{uuid.uuid4().hex[:8]}"
        import_resp = await asgi_client.post("/api/kb/import", json={
            "items": [{
                "ticket_id": test_id,
                "title": "E2E 测试问题单",
                "description": "测试导入功能",
                "root_cause": "测试根因",
                "fix_code": "测试修复",
                "microservice": "test-service",
                "module": "TestModule",
                "error_code": "TEST_ERROR",
                "severity": "P3",
            }]
        })
        assert import_resp.status_code == 200
        import_data = import_resp.json()
        assert import_data["imported"] == 1

        verify_resp = await asgi_client.get(f"/api/kb/tickets?q={test_id}")
        assert verify_resp.status_code == 200
        items = verify_resp.json()["items"]
        found = [i for i in items if i["ticket_id"] == test_id]
        assert len(found) == 1

        delete_resp = await asgi_client.request("DELETE", "/api/kb/tickets", json={"ids": [test_id]})
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] == 1

    @pytest.mark.asyncio
    async def test_kb_import_empty_list(self, asgi_client):
        resp = await asgi_client.post("/api/kb/import", json={"items": []})
        assert resp.status_code == 200
        assert resp.json()["imported"] == 0


# ============================================================================
# V2 端点: 云捷导入
# ============================================================================

class TestV2YunjieImport:
    """V2 /api/v1/yunjie/import（opencode 不可用时返回空列表）。"""

    @pytest.mark.asyncio
    async def test_yunjie_import_no_opencode(self, asgi_client):
        resp = await asgi_client.post("/api/v1/yunjie/import", json={
            "ticket_refs": ["INC-TEST-001"]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "imported" in data
        assert "total" in data
        assert "lightrag_degraded" in data
        assert data["imported"] == 0

    @pytest.mark.asyncio
    async def test_yunjie_import_empty_refs(self, asgi_client):
        resp = await asgi_client.post("/api/v1/yunjie/import", json={
            "ticket_refs": []
        })
        assert resp.status_code == 200
        assert resp.json()["imported"] == 0


# ============================================================================
# V3 端点: SSE 流
# ============================================================================

class TestV3SSEStream:
    """V3 /api/v1/rca/{task_id}/stream SSE 流式推送。"""

    @pytest.mark.asyncio
    async def test_v3_sse_stream_delivers_final_event(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/100",
            "repo": "https://github.com/test/repo",
            "bug_desc": "SSE 流测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]

        events = []
        async with asgi_client.stream("GET", f"/api/v1/rca/{task_id}/stream") as sse:
            async for line in sse.aiter_lines():
                if line.startswith("data: "):
                    evt = json.loads(line[6:])
                    events.append(evt)
                    if evt.get("type") in ("final", "error"):
                        break

        assert len(events) > 0
        final_events = [e for e in events if e["type"] == "final"]
        assert len(final_events) == 1
        assert "top3" in final_events[0]["data"]
        assert "solution" in final_events[0]["data"]

    @pytest.mark.asyncio
    async def test_v3_sse_stream_404(self, asgi_client):
        resp = await asgi_client.get("/api/v1/rca/nonexistent/stream")
        assert resp.status_code == 404


# ============================================================================
# V3 端点: 任务列表
# ============================================================================

class TestV3TaskList:
    """V3 /api/v1/rca/tasks 列出所有任务。"""

    @pytest.mark.asyncio
    async def test_v3_list_tasks(self, asgi_client):
        await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/200",
            "bug_desc": "任务列表测试",
            "runtime_mode": "mock_demo",
        })

        resp = await asgi_client.get("/api/v1/rca/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "statuses" in data
        assert len(data["tasks"]) > 0


# ============================================================================
# V3 端点: HIL 确认
# ============================================================================

class TestV3HILConfirm:
    """V3 /api/v1/rca/{task_id}/confirm 人工确认回灌。"""

    @pytest.mark.asyncio
    async def test_v3_confirm_flow(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/300",
            "bug_desc": "HIL 确认测试 order-center 超卖",
            "repo": "https://github.com/test/repo",
            "runtime_mode": "online_full",
        })
        task_id = resp.json()["task_id"]

        await asyncio.sleep(8)

        state_resp = await asgi_client.get(f"/api/v1/rca/{task_id}/state")
        assert state_resp.status_code == 200
        state = state_resp.json()
        assert state["gate_status"]["hil"] == "pending"

        confirm_resp = await asgi_client.post(
            f"/api/v1/rca/{task_id}/confirm",
            json={
                "confirmed_root_cause_id": state["top3"][0]["located_function"] if state["top3"] else "",
                "operator": "tester",
                "action": "confirm",
                "comment": "E2E 确认",
            }
        )
        assert confirm_resp.status_code == 200
        result = confirm_resp.json()
        assert result["gate_status"]["hil"] == "confirmed"
        assert result["stage"]["status"] == "completed"
        assert result["solution"]["patch_suggestion"]

    @pytest.mark.asyncio
    async def test_v3_confirm_nonexistent_500(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/nonexistent/confirm", json={
            "action": "confirm",
        })
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_v3_confirm_reject(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/301",
            "bug_desc": "HIL 拒绝测试 order-center",
            "repo": "https://github.com/test/repo",
            "runtime_mode": "online_full",
        })
        task_id = resp.json()["task_id"]

        await asyncio.sleep(8)

        confirm_resp = await asgi_client.post(
            f"/api/v1/rca/{task_id}/confirm",
            json={
                "operator": "tester",
                "action": "reject",
                "comment": "误报",
            }
        )
        assert confirm_resp.status_code == 200
        result = confirm_resp.json()
        assert result["gate_status"]["hil"] == "rejected"
        assert result["stage"]["status"] == "rejected"


# ============================================================================
# V3 端点: 断点续跑
# ============================================================================

class TestV3Resume:
    """V3 /api/v1/rca/{task_id}/resume 断点续跑。"""

    @pytest.mark.asyncio
    async def test_v3_resume_completed_task(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/400",
            "bug_desc": "断点续跑测试",
            "repo": "https://github.com/test/repo",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]

        await asyncio.sleep(8)

        result_resp = await asgi_client.get(f"/api/v1/rca/{task_id}")
        assert result_resp.status_code == 200
        assert result_resp.json()["stage"]["status"] == "completed"

        resume_resp = await asgi_client.post(f"/api/v1/rca/{task_id}/resume")
        assert resume_resp.status_code == 200
        resumed = resume_resp.json()
        assert resumed["stage"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_v3_resume_nonexistent_500(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/nonexistent/resume")
        assert resp.status_code == 500


# ============================================================================
# V3 错误场景
# ============================================================================

class TestV3ErrorCases:
    """V3 错误场景: 400/404/409。"""

    @pytest.mark.asyncio
    async def test_v3_analyze_400_missing_fields(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "repo": "https://github.com/test/repo",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_v3_get_result_404(self, asgi_client):
        resp = await asgi_client.get("/api/v1/rca/nonexistent-task")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_v3_get_result_409_not_finished(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://github.com/test/repo/issues/500",
            "bug_desc": "409 测试",
            "runtime_mode": "mock_demo",
        })
        task_id = resp.json()["task_id"]

        result = await asgi_client.get(f"/api/v1/rca/{task_id}")
        assert result.status_code == 409

    @pytest.mark.asyncio
    async def test_v3_get_state_404(self, asgi_client):
        resp = await asgi_client.get("/api/v1/rca/nonexistent/state")
        assert resp.status_code == 404


# ============================================================================
# CodeGraph REST API
# ============================================================================

class TestCodeGraphAPI:
    """CodeGraph node/callers/callees/explore 正常路径 + 404。"""

    SYMBOL = "sym:OrderLockService:acquire"
    NONEXISTENT = "sym:NonExistent:method"

    @pytest.mark.asyncio
    async def test_codegraph_node(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/node/{self.SYMBOL}")
        assert resp.status_code == 200
        node = resp.json()
        assert node["symbol"] == self.SYMBOL
        assert node["file"]
        assert node["line"] > 0

    @pytest.mark.asyncio
    async def test_codegraph_node_404(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/node/{self.NONEXISTENT}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_codegraph_callers(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/callers/{self.SYMBOL}?depth=2")
        assert resp.status_code == 200
        data = resp.json()
        assert "callers" in data
        assert "edges" in data
        assert len(data["callers"]) > 0
        caller_symbols = [c["symbol"] for c in data["callers"]]
        assert "sym:OrderService:submit" in caller_symbols

    @pytest.mark.asyncio
    async def test_codegraph_callers_404(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/callers/{self.NONEXISTENT}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_codegraph_callees(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/callees/{self.SYMBOL}")
        assert resp.status_code == 200
        data = resp.json()
        assert "callees" in data
        assert "edges" in data
        callee_symbols = [c["symbol"] for c in data["callees"]]
        assert "sym:RedisClient:setnx" in callee_symbols

    @pytest.mark.asyncio
    async def test_codegraph_callees_404(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/callees/{self.NONEXISTENT}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_codegraph_explore(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/explore/{self.SYMBOL}?hops=2")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert data["center"] == self.SYMBOL
        assert len(data["nodes"]) > 0

    @pytest.mark.asyncio
    async def test_codegraph_explore_404(self, asgi_client):
        resp = await asgi_client.get(f"/api/v1/codegraph/explore/{self.NONEXISTENT}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_codegraph_node_root_entry(self, asgi_client):
        resp = await asgi_client.get("/api/v1/codegraph/node/sym:OrderController:createOrder")
        assert resp.status_code == 200
        node = resp.json()
        assert node["fan_out"] > 0

    @pytest.mark.asyncio
    async def test_codegraph_callees_root_no_callers(self, asgi_client):
        resp = await asgi_client.get("/api/v1/codegraph/callers/sym:OrderController:createOrder")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["callers"]) == 0


# ============================================================================
# code2cn REST API
# ============================================================================

class TestCode2CnAPI:
    """code2cn generate + outline。"""

    @pytest.mark.asyncio
    async def test_code2cn_generate(self, asgi_client):
        resp = await asgi_client.post("/api/v1/code2cn/generate", json={
            "symbol": "OrderService.submit",
            "file": "OrderService.java",
            "source_code": "public Long submit(OrderReq req) { lock.acquire(req.getSkuId()); stock.deduct(req.getSkuId(), req.getQty()); return orderRepo.save(req); }",
            "language": "java",
            "signature": "submit(OrderReq):Long",
        })
        assert resp.status_code == 200
        outline = resp.json()
        assert outline["symbol"] == "OrderService.submit"
        assert "file" in outline
        assert "degraded" in outline

    @pytest.mark.asyncio
    async def test_code2cn_outline_cached(self, asgi_client):
        gen_resp = await asgi_client.post("/api/v1/code2cn/generate", json={
            "symbol": "TestFunction.compute",
            "file": "Test.java",
            "source_code": "int compute(int x) { return x * 2; }",
            "language": "java",
        })
        assert gen_resp.status_code == 200

        outline_resp = await asgi_client.get("/api/v1/code2cn/outline/TestFunction.compute")
        assert outline_resp.status_code == 200
        outline = outline_resp.json()
        assert outline["symbol"] == "TestFunction.compute"

    @pytest.mark.asyncio
    async def test_code2cn_outline_404(self, asgi_client):
        resp = await asgi_client.get("/api/v1/code2cn/outline/sym:NonExistent:func")
        assert resp.status_code == 404


# ============================================================================
# LightRAG REST API
# ============================================================================

class TestRAGAPI:
    """LightRAG query/insert/insert_kg（降级模式）。"""

    @pytest.mark.asyncio
    async def test_rag_query_degraded(self, asgi_client):
        resp = await asgi_client.post(
            "/api/v1/rag/query",
            params={"query": "订单超卖根因", "intent": "history", "top_k": 5}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "degraded" in data
        assert data["degraded"] is True
        assert "route" in data

    @pytest.mark.asyncio
    async def test_rag_query_propagation_intent(self, asgi_client):
        resp = await asgi_client.post(
            "/api/v1/rag/query",
            params={"query": "调用链追溯", "intent": "propagation"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data

    @pytest.mark.asyncio
    async def test_rag_insert_degraded(self, asgi_client):
        resp = await asgi_client.post(
            "/api/v1/rag/insert",
            params={"text": "测试文本插入", "ids": "test-001"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert "degraded" in data

    @pytest.mark.asyncio
    async def test_rag_insert_kg_degraded(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rag/insert_kg", json={
            "entities": [
                {"entity_name": "OrderService", "type": "function", "description": "订单服务"},
                {"entity_name": "StockService", "type": "function", "description": "库存服务"},
            ],
            "relationships": [
                {"src_id": "OrderService", "tgt_id": "StockService", "description": "calls", "weight": 1.0},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert "degraded" in data


# ============================================================================
# V2 健康检查
# ============================================================================

class TestV2Health:
    """V2 /api/health。"""

    @pytest.mark.asyncio
    async def test_v2_health(self, asgi_client):
        resp = await asgi_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "kb_count" in data
        assert data["kb_count"] > 0
        assert data["version"] == "3.0.0"

    @pytest.mark.asyncio
    async def test_v3_health(self, asgi_client):
        resp = await asgi_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("up", "degraded")
        assert "components" in data
        assert "runtime_mode" in data
        assert "version" in data
