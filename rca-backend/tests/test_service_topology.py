"""Spec5 Task 1/3: 服务级拓扑图 + CONTAINS 关系 UT 测试套件。"""
from __future__ import annotations

import pytest

from app.service_topology import (
    ServiceNode, ServiceCallEdge, ServiceTopology, CmdbRecord,
    TraceSpan, TraceData, CmdbAdapter,
    aggregate_spans_to_topology, enrich_with_cmdb, reverse_propagate,
    build_contains_map, drill_down_functions, drill_down_services,
    build_service_topology_degraded, build_anomaly_path_from_trace,
    ContainsEdge,
)


def _make_trace() -> TraceData:
    """构造跨服务调用 Trace（order → stock → redis）。"""
    return TraceData(
        trace_id="trace-001",
        spans=[
            TraceSpan(span_id="s1", service="order-service", operation="create",
                      parent_span_id=None, duration_ms=3500, error=True),
            TraceSpan(span_id="s2", service="stock-service", operation="deduct",
                      parent_span_id="s1", duration_ms=2800, error=True),
            TraceSpan(span_id="s3", service="redis", operation="getResource",
                      parent_span_id="s2", duration_ms=200, error=False),
        ],
    )


def _make_cmdb() -> CmdbAdapter:
    return CmdbAdapter(records=[
        CmdbRecord(service_id="order-service", service_name="订单服务", owner="alice", team="trade"),
        CmdbRecord(service_id="stock-service", service_name="库存服务", owner="bob", team="inventory"),
    ])


class TestServiceNodeSchema:
    """UT 1: ServiceNode 数据结构"""

    def test_fields(self):
        node = ServiceNode(service_id="svc-1", service_name="订单服务", owner="alice")
        assert node.service_id == "svc-1"
        assert node.degraded is False

    def test_defaults(self):
        node = ServiceNode(service_id="svc-1")
        assert node.service_name == ""
        assert node.degraded is False


class TestSpanAggregation:
    """UT 2: Trace span 聚合为服务节点与跨服务调用边"""

    def test_aggregate_produces_nodes(self):
        topology = aggregate_spans_to_topology(_make_trace())
        service_ids = {n.service_id for n in topology.nodes}
        assert "order-service" in service_ids
        assert "stock-service" in service_ids
        assert "redis" in service_ids

    def test_aggregate_produces_cross_service_edges(self):
        topology = aggregate_spans_to_topology(_make_trace())
        edge_pairs = {(e.src_service, e.tgt_service) for e in topology.edges}
        assert ("order-service", "stock-service") in edge_pairs
        assert ("stock-service", "redis") in edge_pairs

    def test_empty_trace_degrades(self):
        topology = aggregate_spans_to_topology(TraceData(trace_id="empty"))
        assert topology.degraded is True
        assert topology.degradation_reason == "empty_trace"


class TestCmdbEnrichment:
    """UT 3: CMDB 补全服务元数据"""

    def test_enrich_fills_metadata(self):
        topology = aggregate_spans_to_topology(_make_trace())
        enriched = enrich_with_cmdb(topology, _make_cmdb())
        order_node = next(n for n in enriched.nodes if n.service_id == "order-service")
        assert order_node.service_name == "订单服务"
        assert order_node.owner == "alice"
        assert order_node.degraded is False

    def test_partial_cmdb_missing_marks_degraded(self):
        topology = aggregate_spans_to_topology(_make_trace())
        enriched = enrich_with_cmdb(topology, _make_cmdb())  # redis 无 CMDB
        redis_node = next(n for n in enriched.nodes if n.service_id == "redis")
        assert redis_node.degraded is True
        assert enriched.degraded is True


class TestReversePropagation:
    """UT 4: 根因传播遍历（沿调用边反向传播）"""

    def test_reverse_propagation_finds_upstream(self):
        topology = aggregate_spans_to_topology(_make_trace())
        upstream = reverse_propagate(topology, suspect_services=["stock-service"])
        assert "order-service" in upstream  # order 调用 stock，反向找到 order

    def test_reverse_propagation_depth_limit(self):
        topology = aggregate_spans_to_topology(_make_trace())
        upstream = reverse_propagate(topology, suspect_services=["redis"], max_depth=1)
        assert "stock-service" in upstream  # depth=1 找到直接上游
        # depth=2 才能找到 order-service
        upstream_deep = reverse_propagate(topology, suspect_services=["redis"], max_depth=2)
        assert "order-service" in upstream_deep


class TestContainsRelationship:
    """UT 5: CONTAINS 跨层关系映射"""

    def test_build_contains_map(self):
        topology = ServiceTopology(nodes=[
            ServiceNode(service_id="order-service"),
            ServiceNode(service_id="stock-service"),
        ])
        func_map = {
            "OrderService.create": "order-service",
            "OrderService.submit": "order-service",
            "StockService.deduct": "stock-service",
        }
        edges = build_contains_map(topology, func_map)
        assert len(edges) == 3
        service_funcs = {}
        for e in edges:
            service_funcs.setdefault(e.service_id, []).append(e.function_id)
        assert "OrderService.create" in service_funcs["order-service"]
        assert "StockService.deduct" in service_funcs["stock-service"]

    def test_drill_down_functions(self):
        edges = [
            ContainsEdge(service_id="order-service", function_id="OrderService.create"),
            ContainsEdge(service_id="order-service", function_id="OrderService.submit"),
            ContainsEdge(service_id="stock-service", function_id="StockService.deduct"),
        ]
        funcs = drill_down_functions("order-service", edges)
        assert "OrderService.create" in funcs
        assert "OrderService.submit" in funcs
        assert len(funcs) == 2

    def test_drill_down_services(self):
        edges = [
            ContainsEdge(service_id="order-service", function_id="OrderService.create"),
        ]
        svcs = drill_down_services("OrderService.create", edges)
        assert "order-service" in svcs


class TestDegradationFallback:
    """UT 6: Trace/CMDB 缺失降级兜底"""

    def test_trace_missing_uses_fallback(self):
        topology = build_service_topology_degraded(
            trace=None, cmdb=None, fallback_services=["order-service", "stock-service"]
        )
        assert topology.degraded is True
        assert topology.degradation_reason == "trace_missing"
        assert len(topology.nodes) == 2
        for n in topology.nodes:
            assert n.degraded is True

    def test_both_missing_triggers_alert(self):
        topology = build_service_topology_degraded(trace=None, cmdb=None)
        assert topology.degraded is True
        assert topology.degradation_reason == "trace_missing"
        assert len(topology.nodes) == 0


class TestAnomalyPathFromTrace:
    """UT 7: 从 Trace 构造运行时异常路径"""

    def test_build_anomaly_path(self):
        trace = _make_trace()
        path = build_anomaly_path_from_trace(trace, suspect_service="stock-service")
        assert len(path.functions) > 0
        assert path.runtime_anomaly > 0.0
        # propagation_path 应包含错误链路（第一个 error span 回溯到根）
        assert len(path.propagation_path) > 0
        assert "order-service" in path.propagation_path

    def test_empty_trace_empty_path(self):
        path = build_anomaly_path_from_trace(TraceData(trace_id="empty"), "svc")
        assert path.runtime_anomaly == 0.0
