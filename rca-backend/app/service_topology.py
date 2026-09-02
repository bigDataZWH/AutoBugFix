"""Spec5 Task 1: 服务级拓扑图构建。

节点=微服务/接口，边=跨服务调用；Trace span 聚合为服务节点与跨服务调用边；
接入 CMDB 补全服务元数据；根因传播遍历（沿调用边反向传播）；
Trace/CMDB 缺失降级兜底（标注降级标记）。
"""
from __future__ import annotations

from typing import Any, Optional

from .models import AnomalyPath, BaseModel, Field, Literal


# ============================================================================
# 服务级拓扑数据模型
# ============================================================================

class ServiceNode(BaseModel):
    """服务级拓扑节点（微服务 / 接口）。"""
    service_id: str
    service_name: str = ""
    owner: str = ""
    team: str = ""
    endpoint: str = ""
    language: str = ""
    repo: str = ""
    degraded: bool = False  # CMDB 缺失时标注降级


class ServiceCallEdge(BaseModel):
    """跨服务调用边。"""
    src_service: str
    tgt_service: str
    call_type: Literal["rpc", "http", "grpc", "mq", "ffi"] = "rpc"
    weight: float = 1.0
    latency_ms: float = 0.0
    error_rate: float = 0.0
    span_count: int = 0


class ServiceTopology(BaseModel):
    """服务级拓扑图。"""
    nodes: list[ServiceNode] = Field(default_factory=list)
    edges: list[ServiceCallEdge] = Field(default_factory=list)
    degraded: bool = False  # Trace 缺失时标注降级
    degradation_reason: str = ""


class CmdbRecord(BaseModel):
    """CMDB 服务元数据。"""
    service_id: str
    service_name: str = ""
    owner: str = ""
    team: str = ""
    language: str = ""
    repo: str = ""
    endpoints: list[str] = Field(default_factory=list)


# ============================================================================
# Trace span 聚合
# ============================================================================

class TraceSpan(BaseModel):
    """单个 Trace span。"""
    span_id: str
    trace_id: str = ""
    parent_span_id: Optional[str] = None
    service: str = ""
    operation: str = ""
    start_ms: float = 0.0
    duration_ms: float = 0.0
    error: bool = False
    tags: dict[str, Any] = Field(default_factory=dict)


class TraceData(BaseModel):
    """完整 Trace 数据（span 树）。"""
    trace_id: str
    spans: list[TraceSpan] = Field(default_factory=list)
    degraded: bool = False


def aggregate_spans_to_topology(trace: TraceData) -> ServiceTopology:
    """将 Trace span 树聚合为服务级节点与跨服务调用边。

    - 节点：每个出现过的 service 聚合为一个 ServiceNode
    - 边：parent_span_id != None 且 parent.service != span.service 时，构造跨服务调用边
    """
    if not trace.spans:
        return ServiceTopology(degraded=True, degradation_reason="empty_trace")

    services: dict[str, ServiceNode] = {}
    edges: dict[tuple[str, str], ServiceCallEdge] = {}

    span_index = {s.span_id: s for s in trace.spans}

    for span in trace.spans:
        # 聚合服务节点
        if span.service and span.service not in services:
            services[span.service] = ServiceNode(
                service_id=span.service,
                service_name=span.service,
                endpoint=span.operation,
                degraded=True,  # CMDB 未补全前标注降级
            )

        # 跨服务调用边（基于 parent-child span）
        if span.parent_span_id and span.parent_span_id in span_index:
            parent = span_index[span.parent_span_id]
            if parent.service and span.service and parent.service != span.service:
                key = (parent.service, span.service)
                if key not in edges:
                    edges[key] = ServiceCallEdge(
                        src_service=parent.service,
                        tgt_service=span.service,
                        call_type="rpc",
                        weight=1.0,
                    )
                edge = edges[key]
                edge.span_count += 1
                edge.latency_ms = max(edge.latency_ms, span.duration_ms)
                if span.error:
                    edge.error_rate = min(edge.error_rate + 0.1, 1.0)

    return ServiceTopology(
        nodes=list(services.values()),
        edges=list(edges.values()),
        degraded=False,
    )


# ============================================================================
# CMDB 补全
# ============================================================================

class CmdbAdapter:
    """CMDB 适配器（可 mock）。"""
    def __init__(self, records: Optional[list[CmdbRecord]] = None) -> None:
        self._records: dict[str, CmdbRecord] = {}
        if records:
            for r in records:
                self._records[r.service_id] = r

    def fetch(self, service_id: str) -> Optional[CmdbRecord]:
        return self._records.get(service_id)

    def fetch_batch(self, service_ids: list[str]) -> dict[str, CmdbRecord]:
        return {sid: self._records[sid] for sid in service_ids if sid in self._records}


def enrich_with_cmdb(
    topology: ServiceTopology,
    cmdb: CmdbAdapter,
) -> ServiceTopology:
    """用 CMDB 补全服务节点元数据，清除降级标记。"""
    if not topology.nodes:
        return topology

    cmdb_missing_count = 0
    for node in topology.nodes:
        record = cmdb.fetch(node.service_id)
        if record is None:
            cmdb_missing_count += 1
            node.degraded = True
            continue
        node.service_name = record.service_name or node.service_name
        node.owner = record.owner
        node.team = record.team
        node.language = record.language
        node.repo = record.repo
        node.degraded = False

    if cmdb_missing_count == len(topology.nodes):
        topology.degraded = True
        topology.degradation_reason = "cmdb_all_missing"
    elif cmdb_missing_count > 0:
        topology.degraded = True
        topology.degradation_reason = f"cmdb_partial_missing:{cmdb_missing_count}"

    return topology


# ============================================================================
# 根因传播遍历（沿调用边反向传播）
# ============================================================================

def reverse_propagate(
    topology: ServiceTopology,
    suspect_services: list[str],
    max_depth: int = 3,
) -> list[str]:
    """沿调用边反向传播：从嫌疑服务出发，找出上游调用者。

    返回包含 suspect_services 与其上游（调用方）服务的列表。
    """
    if not topology.edges:
        return list(suspect_services)

    # 构建反向邻接表：tgt -> [src, ...]
    reverse_adj: dict[str, list[str]] = {}
    for edge in topology.edges:
        reverse_adj.setdefault(edge.tgt_service, []).append(edge.src_service)

    visited: set[str] = set(suspect_services)
    current_layer = list(suspect_services)

    for _ in range(max_depth):
        if not current_layer:
            break
        next_layer: list[str] = []
        for svc in current_layer:
            for upstream in reverse_adj.get(svc, []):
                if upstream not in visited:
                    visited.add(upstream)
                    next_layer.append(upstream)
        current_layer = next_layer

    return list(visited)


# ============================================================================
# CONTAINS 跨层关系映射（Spec5 Task 3）
# ============================================================================

class ContainsEdge(BaseModel):
    """服务→函数 CONTAINS 下钻边。"""
    service_id: str
    function_id: str
    function_symbol: str = ""


def build_contains_map(
    service_topology: ServiceTopology,
    function_service_map: dict[str, str],
) -> list[ContainsEdge]:
    """建立服务级节点 → 函数级节点的 CONTAINS 下钻边。

    Args:
        service_topology: 服务级拓扑
        function_service_map: function_id -> service_id 映射

    Returns:
        CONTAINS 边列表（service_id 复合键格式：service::function）
    """
    service_ids = {n.service_id for n in service_topology.nodes}
    edges: list[ContainsEdge] = []
    for func_id, svc_id in function_service_map.items():
        if svc_id in service_ids:
            edges.append(ContainsEdge(
                service_id=svc_id,
                function_id=func_id,
                function_symbol=func_id.split("::")[-1] if "::" in func_id else func_id,
            ))
    return edges


def drill_down_functions(
    service_id: str,
    contains_edges: list[ContainsEdge],
) -> list[str]:
    """服务→函数下钻查询：返回该服务包含的全部函数 ID。"""
    return [
        edge.function_id
        for edge in contains_edges
        if edge.service_id == service_id
    ]


def drill_down_services(
    function_id: str,
    contains_edges: list[ContainsEdge],
) -> list[str]:
    """函数→服务反向查询：返回包含该函数的服务 ID。"""
    return [
        edge.service_id
        for edge in contains_edges
        if edge.function_id == function_id
    ]


# ============================================================================
# Trace 缺失/CMDB 缺失降级兜底
# ============================================================================

def build_service_topology_degraded(
    trace: Optional[TraceData] = None,
    cmdb: Optional[CmdbAdapter] = None,
    fallback_services: Optional[list[str]] = None,
) -> ServiceTopology:
    """统一入口：处理 Trace/CMDB 双缺场景。

    - Trace 缺失：使用 fallback_services 构造降级拓扑
    - CMDB 缺失：节点标注 degraded=True
    - 双缺：触发降级告警
    """
    if trace is None or trace.degraded or not trace.spans:
        # Trace 缺失降级
        services = fallback_services or []
        topology = ServiceTopology(
            nodes=[
                ServiceNode(service_id=s, service_name=s, degraded=True)
                for s in services
            ],
            degraded=True,
            degradation_reason="trace_missing",
        )
        return topology

    topology = aggregate_spans_to_topology(trace)
    if cmdb is not None:
        topology = enrich_with_cmdb(topology, cmdb)

    return topology


# ============================================================================
# AnomalyPath 构造辅助（Spec5 Task 1.2 关联）
# ============================================================================

def build_anomaly_path_from_trace(
    trace: TraceData,
    suspect_service: str = "",
) -> AnomalyPath:
    """从 Trace 数据构造运行时异常路径 P_runtime。"""
    if not trace.spans:
        return AnomalyPath(
            propagation_path=[suspect_service] if suspect_service else [],
            functions=[],
            runtime_anomaly=0.0,
        )

    # 找出错误 span 与其祖先链
    span_index = {s.span_id: s for s in trace.spans}
    error_spans = [s for s in trace.spans if s.error]

    propagation_path: list[str] = []
    functions: list[str] = []

    if error_spans:
        # 取第一个错误 span，回溯到根
        err = error_spans[0]
        chain: list[TraceSpan] = [err]
        current = err
        while current.parent_span_id and current.parent_span_id in span_index:
            current = span_index[current.parent_span_id]
            chain.append(current)
        chain.reverse()
        propagation_path = [s.service for s in chain]
        functions = [f"{s.service}::{s.operation}" for s in chain]

    anomaly_score = min(len(error_spans) / max(len(trace.spans), 1), 1.0)

    return AnomalyPath(
        span_tree={"trace_id": trace.trace_id, "span_count": len(trace.spans)},
        propagation_path=propagation_path,
        functions=functions,
        runtime_anomaly=anomaly_score,
    )
