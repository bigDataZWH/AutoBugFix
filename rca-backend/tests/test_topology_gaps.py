"""P2-J: 服务拓扑测试缺口（J4 span聚合细节、J5 反向传播边界、J6 降级入口边界）。"""
from __future__ import annotations

import pytest

from app.service_topology import (
    ServiceNode, ServiceCallEdge, ServiceTopology, CmdbRecord,
    TraceSpan, TraceData, CmdbAdapter, ContainsEdge,
    aggregate_spans_to_topology, enrich_with_cmdb, reverse_propagate,
    build_contains_map, drill_down_functions, drill_down_services,
    build_service_topology_degraded, build_anomaly_path_from_trace,
)


def _make_span(
    span_id: str,
    service: str,
    operation: str = "op",
    parent_span_id: str | None = None,
    duration_ms: float = 100.0,
    error: bool = False,
) -> TraceSpan:
    return TraceSpan(
        span_id=span_id,
        trace_id="trace-001",
        service=service,
        operation=operation,
        parent_span_id=parent_span_id,
        duration_ms=duration_ms,
        error=error,
    )


# ============================================================================
# J4: Span 聚合边细节
# ============================================================================

class TestJ4SpanAggregationEdgeDetails:
    """J4: 聚合边的 span_count / latency_ms / error_rate 细节。"""

    def test_j4a_multiple_spans_increment_span_count(self):
        """同一服务对的多次调用应聚合 span_count > 1。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None),
            _make_span("s2", "stock", parent_span_id="s1"),
            _make_span("s3", "stock", parent_span_id="s1"),
        ])
        topo = aggregate_spans_to_topology(trace)
        edge = next(e for e in topo.edges if e.src_service == "order" and e.tgt_service == "stock")
        assert edge.span_count == 2

    def test_j4b_latency_ms_tracks_max(self):
        """latency_ms 应取所有 span 中最大 duration。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None, duration_ms=500),
            _make_span("s2", "stock", parent_span_id="s1", duration_ms=300),
            _make_span("s3", "stock", parent_span_id="s1", duration_ms=900),
        ])
        topo = aggregate_spans_to_topology(trace)
        edge = next(e for e in topo.edges if e.src_service == "order" and e.tgt_service == "stock")
        assert edge.latency_ms == 900

    def test_j4c_error_rate_accumulates_and_caps(self):
        """error_rate 累加 0.1/错误span，上限 1.0。"""
        spans = [_make_span("s1", "order", parent_span_id=None)]
        for i in range(15):
            spans.append(_make_span(f"s{i+2}", "stock", parent_span_id="s1", error=True))
        trace = TraceData(trace_id="t1", spans=spans)
        topo = aggregate_spans_to_topology(trace)
        edge = next(e for e in topo.edges if e.src_service == "order" and e.tgt_service == "stock")
        assert edge.error_rate == 1.0

    def test_j4d_same_service_spans_no_edge(self):
        """父span和子span同服务时不应创建跨服务边。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None),
            _make_span("s2", "order", parent_span_id="s1"),
        ])
        topo = aggregate_spans_to_topology(trace)
        assert len(topo.edges) == 0
        assert len(topo.nodes) == 1

    def test_j4e_orphan_span_safely_skipped(self):
        """parent_span_id 指向不存在的 span 时安全跳过边创建。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None),
            _make_span("s2", "stock", parent_span_id="nonexistent"),
        ])
        topo = aggregate_spans_to_topology(trace)
        assert len(topo.nodes) == 2
        assert len(topo.edges) == 0


# ============================================================================
# J5: 反向传播边界
# ============================================================================

class TestJ5ReversePropagationEdgeCases:
    """J5: reverse_propagate 的边界场景。"""

    def test_j5a_empty_edges_returns_suspects_as_is(self):
        """拓扑无边时，原样返回嫌疑服务列表。"""
        topo = ServiceTopology(nodes=[ServiceNode(service_id="a")], edges=[])
        result = reverse_propagate(topo, suspect_services=["a"])
        assert result == ["a"]

    def test_j5b_max_depth_zero_returns_only_suspects(self):
        """max_depth=0 时仅返回嫌疑服务，不遍历上游。"""
        topo = ServiceTopology(
            nodes=[ServiceNode(service_id="a"), ServiceNode(service_id="b")],
            edges=[ServiceCallEdge(src_service="b", tgt_service="a")],
        )
        result = reverse_propagate(topo, suspect_services=["a"], max_depth=0)
        assert result == ["a"]
        assert "b" not in result

    def test_j5c_circular_chain_terminates(self):
        """循环调用链 A→B→A 不应死循环。"""
        topo = ServiceTopology(
            nodes=[ServiceNode(service_id="a"), ServiceNode(service_id="b")],
            edges=[
                ServiceCallEdge(src_service="a", tgt_service="b"),
                ServiceCallEdge(src_service="b", tgt_service="a"),
            ],
        )
        result = reverse_propagate(topo, suspect_services=["a"], max_depth=10)
        assert "a" in result
        assert "b" in result

    def test_j5d_suspect_not_in_edges_still_returned(self):
        """嫌疑服务不在任何边中仍应出现在结果列表。"""
        topo = ServiceTopology(
            nodes=[ServiceNode(service_id="a"), ServiceNode(service_id="b")],
            edges=[ServiceCallEdge(src_service="b", tgt_service="a")],
        )
        result = reverse_propagate(topo, suspect_services=["unknown-svc"])
        assert "unknown-svc" in result


# ============================================================================
# J6: build_service_topology_degraded + CMDB 边界
# ============================================================================

class TestJ6DegradedEntryAndCmdb:
    """J6: 统一降级入口与 CMDB 补全边界。"""

    def test_j6a_trace_present_cmdb_present_not_degraded(self):
        """Trace 和 CMDB 均在 → 正常路径，拓扑不降级。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None),
            _make_span("s2", "stock", parent_span_id="s1"),
        ])
        cmdb = CmdbAdapter(records=[
            CmdbRecord(service_id="order", service_name="订单服务", owner="alice"),
            CmdbRecord(service_id="stock", service_name="库存服务", owner="bob"),
        ])
        topo = build_service_topology_degraded(trace=trace, cmdb=cmdb)
        assert topo.degraded is False
        order = next(n for n in topo.nodes if n.service_id == "order")
        assert order.owner == "alice"
        assert order.degraded is False

    def test_j6b_trace_present_cmdb_none_nodes_degraded(self):
        """Trace 在但 CMDB=None → 节点保持 degraded=True（聚合阶段设置）。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None),
            _make_span("s2", "stock", parent_span_id="s1"),
        ])
        topo = build_service_topology_degraded(trace=trace, cmdb=None)
        for n in topo.nodes:
            assert n.degraded is True

    def test_j6c_trace_degraded_flag_triggers_missing_path(self):
        """trace.degraded=True → 走 trace_missing 降级路径。"""
        trace = TraceData(trace_id="t1", degraded=True, spans=[
            _make_span("s1", "order", parent_span_id=None),
        ])
        topo = build_service_topology_degraded(trace=trace, fallback_services=["fallback"])
        assert topo.degraded is True
        assert topo.degradation_reason == "trace_missing"
        assert len(topo.nodes) == 1
        assert topo.nodes[0].service_id == "fallback"

    def test_j6d_empty_spans_list_triggers_missing_path(self):
        """spans=[] → 走 trace_missing 降级路径。"""
        trace = TraceData(trace_id="t1", spans=[])
        topo = build_service_topology_degraded(trace=trace, fallback_services=["a", "b"])
        assert topo.degraded is True
        assert topo.degradation_reason == "trace_missing"
        assert len(topo.nodes) == 2


class TestJ6CmdbEnrichmentEdgeCases:
    """J6 补充: enrich_with_cmdb 边界。"""

    def test_j6e_empty_topology_nodes_unchanged(self):
        """拓扑无节点时 enrich 直接返回原拓扑。"""
        topo = ServiceTopology(nodes=[], edges=[], degraded=False)
        cmdb = CmdbAdapter(records=[CmdbRecord(service_id="x")])
        result = enrich_with_cmdb(topo, cmdb)
        assert result.nodes == []

    def test_j6f_all_cmdb_missing(self):
        """全部节点 CMDB 缺失 → cmdb_all_missing 降级。"""
        topo = ServiceTopology(nodes=[
            ServiceNode(service_id="a", degraded=True),
            ServiceNode(service_id="b", degraded=True),
        ])
        result = enrich_with_cmdb(topo, CmdbAdapter(records=[]))
        assert result.degraded is True
        assert result.degradation_reason == "cmdb_all_missing"
        for n in result.nodes:
            assert n.degraded is True


# ============================================================================
# J6 补充: build_anomaly_path_from_trace 边界
# ============================================================================

class TestJ6AnomalyPathEdgeCases:
    """build_anomaly_path_from_trace 边界场景。"""

    def test_no_error_spans_empty_path(self):
        """Trace 无错误 span → propagation_path 为空、anomaly=0。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None, error=False),
            _make_span("s2", "stock", parent_span_id="s1", error=False),
        ])
        path = build_anomaly_path_from_trace(trace)
        assert path.propagation_path == []
        assert path.functions == []
        assert path.runtime_anomaly == 0.0

    def test_empty_trace_with_suspect_returns_suspect_in_path(self):
        """空 Trace + suspect_service → path 包含 suspect_service。"""
        path = build_anomaly_path_from_trace(TraceData(trace_id="empty"), suspect_service="svc-x")
        assert "svc-x" in path.propagation_path
        assert path.runtime_anomaly == 0.0

    def test_anomaly_score_ratio(self):
        """anomaly_score = error_spans / total_spans。"""
        trace = TraceData(trace_id="t1", spans=[
            _make_span("s1", "order", parent_span_id=None, error=True),
            _make_span("s2", "stock", parent_span_id="s1", error=False),
            _make_span("s3", "redis", parent_span_id="s2", error=True),
        ])
        path = build_anomaly_path_from_trace(trace)
        assert path.runtime_anomaly == pytest.approx(2 / 3)


# ============================================================================
# J6 补充: CONTAINS 关系边界
# ============================================================================

class TestJ6ContainsEdgeCases:
    """CONTAINS 映射与下钻的边界场景。"""

    def test_build_contains_map_filters_unknown_service(self):
        """function_service_map 中映射到不存在服务的函数应被过滤。"""
        topo = ServiceTopology(nodes=[ServiceNode(service_id="order")])
        func_map = {
            "OrderService.create": "order",
            "UnknownService.run": "nonexistent",
        }
        edges = build_contains_map(topo, func_map)
        assert len(edges) == 1
        assert edges[0].function_id == "OrderService.create"

    def test_drill_down_functions_no_match(self):
        """无匹配服务的下钻返回空列表。"""
        edges = [ContainsEdge(service_id="order", function_id="OrderService.create")]
        assert drill_down_functions("nonexistent", edges) == []

    def test_drill_down_services_no_match(self):
        """无匹配函数的反向查询返回空列表。"""
        edges = [ContainsEdge(service_id="order", function_id="OrderService.create")]
        assert drill_down_services("NonExistent", edges) == []

    def test_build_contains_map_symbol_extraction(self):
        """function_symbol 应从复合 ID 中提取最后一段。"""
        topo = ServiceTopology(nodes=[ServiceNode(service_id="order")])
        func_map = {"order::OrderService::create": "order"}
        edges = build_contains_map(topo, func_map)
        assert edges[0].function_symbol == "create"
