from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ============ 分析请求/响应 ============
class AnalyzeRequest(BaseModel):
    mr_url: Optional[str] = Field(None, description="CodeHub MR 链接")
    repo: Optional[str] = Field(None, description="代码仓(命名空间/项目名 或 ID)")
    branch: Optional[str] = Field(None, description="分支")
    ticket_url: Optional[str] = Field(None, description="问题单链接(可选)")
    pasted_content: Optional[str] = Field(
        None, description="无 CodeHub 时, 直接粘贴的 MR/问题单文本"
    )
    depth: Literal["quick", "standard", "deep"] = "standard"


class CodeRef(BaseModel):
    file: str
    lines: Optional[str] = None
    snippet: Optional[str] = None
    explanation: Optional[str] = None


class RootCause(BaseModel):
    summary: str
    category: Optional[str] = None
    contributing_factors: List[str] = Field(default_factory=list)
    evidence: List[CodeRef] = Field(default_factory=list)
    severity: Optional[str] = None


class MatchedCase(BaseModel):
    id: str
    title: str
    root_cause: Optional[str] = None
    verification: Optional[str] = None
    code_snippet: Optional[str] = None
    code_path: Optional[str] = None
    language: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    similarity: float = 0.0
    source_url: Optional[str] = None


class BestPractice(BaseModel):
    title: str
    description: str
    source: Optional[str] = None
    applicability: Optional[str] = None


class CodeChange(BaseModel):
    file: str
    change_type: Optional[str] = None
    description: str
    patch: Optional[str] = None


class DesignSolution(BaseModel):
    approach: str
    rationale: str
    code_changes: List[CodeChange] = Field(default_factory=list)
    tradeoffs: List[str] = Field(default_factory=list)
    prevention: List[str] = Field(default_factory=list)


class VerificationSuggestion(BaseModel):
    steps: List[str] = Field(default_factory=list)
    test_cases: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


class MRSummary(BaseModel):
    mr_iid: Optional[str] = None
    title: Optional[str] = None
    source_branch: Optional[str] = None
    target_branch: Optional[str] = None
    author: Optional[str] = None
    state: Optional[str] = None
    changed_files: int = 0
    description: Optional[str] = None
    diff_stats: Optional[Dict[str, Any]] = None
    changed_file_paths: List[str] = Field(default_factory=list)
    diff: Optional[str] = None


class AnalyzeResponse(BaseModel):
    task_id: str
    status: Literal["ok", "partial", "error"] = "ok"
    warnings: List[str] = Field(default_factory=list)
    mr: Optional[MRSummary] = None
    root_cause: Optional[RootCause] = None
    matched_cases: List[MatchedCase] = Field(default_factory=list)
    best_practices: List[BestPractice] = Field(default_factory=list)
    design_solution: Optional[DesignSolution] = None
    verification: Optional[VerificationSuggestion] = None
    elapsed_ms: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============ 知识库 API ============
class KnowledgeSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: List[MatchedCase]


class IngestResult(BaseModel):
    ingested: int = 0
    skipped: int = 0
    errors: List[str] = Field(default_factory=list)


class KnowledgeStats(BaseModel):
    total: int = 0
    last_updated: Optional[str] = None
    embed_provider: Optional[str] = None


# ============ 健康/设置 ============
class HealthResponse(BaseModel):
    status: str = "ok"
    llm_configured: bool = False
    codehub_configured: bool = False
    codehub_mock: bool = False
    embed_provider: str = "api"
    kb_count: int = 0
    version: str = "0.1.0"


class TestResult(BaseModel):
    ok: bool
    message: str
    detail: Optional[str] = None
