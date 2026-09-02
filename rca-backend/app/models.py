from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================================
# V2 兼容模型（保留）
# ============================================================================

class AnalyzeRequestV2(BaseModel):
    ticket_url: str = Field(..., description="问题单链接")
    repo_url: str = Field(..., description="代码仓库地址")
    branch: str = Field(default="main", description="分支")
    microservice: Optional[str] = Field(default=None, description="微服务模块")
    description: Optional[str] = Field(default=None, description="问题描述(无链接时)")
    depth: str = Field(default="standard", description="分析深度 quick|standard|deep")


class KBImportItem(BaseModel):
    ticket_id: str
    title: str
    description: str
    root_cause: str
    fix_code: str
    microservice: Optional[str] = None
    module: Optional[str] = None
    error_code: Optional[str] = None
    severity: Optional[str] = None


class KBImportRequest(BaseModel):
    items: list[KBImportItem]


class RootCauseFactor(BaseModel):
    kind: str
    desc: str
    evidence: str = ""


class CallStackNode(BaseModel):
    symbol: str
    file: str
    line: int
    score: float = 0.0
    reason: str = ""


class KBMatch(BaseModel):
    similarity: float
    ticket_id: str
    title: str
    root_cause: str
    fix_code: str = ""
    microservice: str = ""
    error_code: str = ""


class BestPractice(BaseModel):
    title: str
    content: str
    source: str


class SolutionDiff(BaseModel):
    file: str
    before: str
    after: str
    summary: str


class SolutionStep(BaseModel):
    step: int
    action: str
    detail: str


class RCA(BaseModel):
    root_cause: str
    confidence: float
    factors: list[RootCauseFactor] = []
    call_stack: list[CallStackNode] = []


class Solution(BaseModel):
    diffs: list[SolutionDiff] = []
    steps: list[SolutionStep] = []
    verify_expected: dict[str, Any] = {}
    patch_suggestion: str = ""
    test_cases: list[str] = Field(default_factory=list)
    historical_cases: list[str] = Field(default_factory=list)
    best_practices: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    task_id: str
    confidence: float
    elapsed_s: float
    degraded: bool = False
    rca: RCA
    kb_matches: list[KBMatch] = []
    best_practices: list[BestPractice] = []
    solution: Solution


class StageEvent(BaseModel):
    stage_index: int
    stage_name: str
    status: str
    log: str
    timestamp: float


# ============================================================================
# Spec 1: 代码中文描述
# ============================================================================

class CodeOutline(BaseModel):
    symbol: str
    file: str
    cn_summary: str = ""
    external_calls: list[str] = Field(default_factory=list)
    failure_paths: list[str] = Field(default_factory=list)
    degraded: bool = False
    model: Optional[str] = None
    tokens: dict[str, int] = Field(default_factory=dict)
    cached: bool = False


class AstFunctionNode(BaseModel):
    symbol: str
    file: str
    start_line: int
    end_line: int
    source_code: str
    language: Literal["java", "go", "python", "typescript"] = "java"
    signature: str = ""


class Code2CnRequest(BaseModel):
    symbol: str
    file: str = ""
    source_code: str = ""
    language: str = "java"
    signature: str = ""


# ============================================================================
# Spec 2: CodeGraph 代码图谱
# ============================================================================

class CPGNode(BaseModel):
    symbol: str
    type: Literal["function", "class", "method"] = "method"
    file: str = ""
    line: int = 0
    fan_in: int = 0
    fan_out: int = 0
    complexity: int = 0
    cn_summary: Optional[str] = None
    signature: str = ""
    source_code: str = ""


class CPGEdge(BaseModel):
    src: str
    tgt: str
    type: Literal["call", "inherit", "ref"] = "call"
    weight: float = 1.0
    file: str = ""
    line: int = 0


class CallersResponse(BaseModel):
    callers: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    truncated: bool = False


class CalleesResponse(BaseModel):
    callees: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class ExploreResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    center: str


class TaintResponse(BaseModel):
    paths: list[dict[str, Any]]
    entry_found: bool
    sink_found: bool


# ============================================================================
# Spec 3: LightRAG 检索引擎
# ============================================================================

class AstKgEntity(BaseModel):
    entity_name: str
    type: str = "function"
    description: str = ""


class AstKgRelationship(BaseModel):
    src_id: str
    tgt_id: str
    description: str = "calls"
    weight: float = 1.0


class AstKg(BaseModel):
    entities: list[AstKgEntity] = Field(default_factory=list)
    relationships: list[AstKgRelationship] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    mode: Literal["low_level", "high_level", "hybrid"] = "hybrid"
    content: str = ""
    top_k: int = 60
    elapsed_ms: int = 0
    degraded: bool = False
    route: str = ""


# ============================================================================
# Spec 5: 双图谱交叉验证
# ============================================================================

class SuspectFunction(BaseModel):
    function_id: str
    function_name: str = ""
    call_path: list[str] = Field(default_factory=list)
    static_depth: float = 0.0
    file: str = ""
    line: int = 0


class AnomalyPath(BaseModel):
    span_tree: dict[str, Any] = Field(default_factory=dict)
    propagation_path: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    runtime_anomaly: float = 0.0


class MetricAnomalies(BaseModel):
    functions: dict[str, float] = Field(default_factory=dict)
    services: dict[str, float] = Field(default_factory=dict)
    degraded: bool = False


class ChangeRecord(BaseModel):
    function_id: str
    timestamp: float = 0.0
    commits: int = 0


class ChangeRecords(BaseModel):
    records: list[ChangeRecord] = Field(default_factory=list)
    degraded: bool = False


class Evidence(BaseModel):
    static_depth: float = 0.0
    runtime_anomaly: float = 0.0
    metric_corr: float = 0.0
    change_recency: float = 0.0


class Candidate(BaseModel):
    function_id: str
    function_name: str = ""
    file: str = ""
    line: int = 0
    score: float = 0.0
    evidence: Evidence = Field(default_factory=Evidence)
    hit_kind: Literal["intersection", "static_only", "runtime_only"] = "intersection"


class WeightConfig(BaseModel):
    w1_static_depth: float = 0.3
    w2_runtime_anomaly: float = 0.3
    w3_metric_corr: float = 0.2
    w4_change_recency: float = 0.2
    top_k: int = 3
    single_path_allowed: bool = True
    metric_missing_rebalance: bool = True
    change_missing_rebalance: bool = True


# ============================================================================
# Spec 6: 双闸门与知识飞轮
# ============================================================================

class CragTriage(BaseModel):
    verdict: Literal["relevant", "ambiguous", "irrelevant"] = "relevant"
    refined_evidence: list[dict[str, Any]] = Field(default_factory=list)
    augmented_query: Optional[str] = None
    rewritten_query: Optional[str] = None


class PanelPayload(BaseModel):
    task_id: str = ""
    reason: str = ""
    top3: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    threshold: float = 0.0
    suggested_root_cause: str = ""


class HilResult(BaseModel):
    action: Literal["pass", "hang"] = "pass"
    panel_payload: Optional[PanelPayload] = None


class HilDecision(BaseModel):
    task_id: str
    action: Literal["confirm", "modify", "reject"] = "confirm"
    modified_top3: Optional[list[dict[str, Any]]] = None
    feedback: Optional[str] = None


class FlywheelPayload(BaseModel):
    root_cause: str = ""
    root_cause_function: str = ""
    call_path: list[str] = Field(default_factory=list)
    fix_patch: str = ""
    verify_case: str = ""
    ticket_id: str = ""
    title: str = ""
    description: str = ""


class SimilarToEdge(BaseModel):
    src_id: str
    tgt_id: str
    type: Literal["SIMILAR_TO"] = "SIMILAR_TO"
    weight: float = 0.0


class WritebackResult(BaseModel):
    inserted: int = 0
    similar_edges: list[SimilarToEdge] = Field(default_factory=list)


class GateStatus(BaseModel):
    crag: Literal["relevant", "ambiguous", "irrelevant", "passed"] = "relevant"
    hil: Literal["pending", "confirmed", "modified", "rejected", "skipped"] = "skipped"


# ============================================================================
# Spec 4: 5-Agent 智能引擎
# ============================================================================

class BugInfo(BaseModel):
    bug_id: str = ""
    title: str = ""
    description: str = ""
    error_type: str = ""
    stack: list[str] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)
    link: str = ""


class A1Output(BaseModel):
    symptoms: list[str] = Field(default_factory=list)
    error_type: str = ""
    query: str = ""
    suspect_services: list[str] = Field(default_factory=list)


class RootCause(BaseModel):
    root_cause: str = ""
    confidence: float = 0.0
    evidence_chain: list[str] = Field(default_factory=list)
    located_function: str = ""
    file: str = ""
    line: int = 0


class Stage(BaseModel):
    index: int = 0
    name: str = ""
    status: str = "pending"
    artifact: dict[str, Any] = Field(default_factory=dict)


class RCAState(BaseModel):
    bug_info: BugInfo = Field(default_factory=BugInfo)
    symptoms: list[str] = Field(default_factory=list)
    error_type: str = ""
    query: str = ""
    suspect_services: list[str] = Field(default_factory=list)
    S_static: list[SuspectFunction] = Field(default_factory=list)
    P_runtime: AnomalyPath = Field(default_factory=AnomalyPath)
    top3: list[RootCause] = Field(default_factory=list)
    gate_status: GateStatus = Field(default_factory=GateStatus)
    solution: Solution = Field(default_factory=Solution)
    stage: Stage = Field(default_factory=Stage)
    task_id: str = ""
    degraded: bool = False
    runtime_mode: Literal["online_full", "offline_light", "mock_demo"] = "online_full"


class AnalyzeRequest(BaseModel):
    bug_id: str = ""
    symptom: str = ""
    error_type: Optional[str] = None
    suspect_service: Optional[str] = None
    repo: str = ""
    branch: str = "main"
    bug_link: str = ""
    bug_desc: str = ""
    runtime_mode: Literal["online_full", "offline_light", "mock_demo"] = "online_full"
    depth: str = "standard"


class AnalyzeResponse(BaseModel):
    task_id: str
    status: str = "queued"
    runtime_mode: str = "online_full"


class ConfirmRequest(BaseModel):
    confirmed_root_cause_id: str = ""
    operator: str = ""
    comment: Optional[str] = None
    action: Literal["confirm", "modify", "reject"] = "confirm"
    modified_top3: Optional[list[dict[str, Any]]] = None
    feedback: Optional[str] = None
