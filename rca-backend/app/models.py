from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
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
    verify_expected: dict = {}


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
