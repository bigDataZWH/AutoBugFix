from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, List

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile

from app.config import settings, get_settings
from app.knowledge.schema import KnowledgeRecordIn
from app.knowledge.store import KnowledgeStore
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    HealthResponse,
    IngestResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeStats,
    MatchedCase,
    TestResult,
)
from app.services.analyzer import Analyzer
from app.services.best_practice import BestPracticeExplorer
from app.services.codehub import CodeHubClient
from app.services.llm import EmbeddingClient, LLMClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# ============ 模块级单例 ============
settings.ensure_dirs()

_settings = get_settings()
llm_client = LLMClient(_settings)
embed_client = EmbeddingClient(_settings)
codehub_client = CodeHubClient(_settings)
store = KnowledgeStore(_settings, embed_client)
analyzer = Analyzer(_settings, codehub_client, llm_client, embed_client, store)
bp_explorer = BestPracticeExplorer(_settings, llm_client)


# ============ 依赖注入 ============
def get_settings_dep():
    return _settings


def get_llm() -> LLMClient:
    return llm_client


def get_embed() -> EmbeddingClient:
    return embed_client


def get_codehub() -> CodeHubClient:
    return codehub_client


def get_store() -> KnowledgeStore:
    return store


def get_analyzer() -> Analyzer:
    return analyzer


def get_bp_explorer() -> BestPracticeExplorer:
    return bp_explorer


# ============ 分析 ============
@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest, a: Analyzer = Depends(get_analyzer)) -> AnalyzeResponse:
    """根因分析主入口: CodeHub → LLM → 知识库 → 设计方案 → 验证建议。"""
    try:
        return a.analyze(req)
    except Exception as e:
        logger.exception("analyze 编排异常")
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


# ============ 知识库 ============
@router.post("/knowledge/search", response_model=KnowledgeSearchResponse)
def knowledge_search(
    req: KnowledgeSearchRequest, s: KnowledgeStore = Depends(get_store)
) -> KnowledgeSearchResponse:
    results: List[MatchedCase] = s.search(req.query, top_k=req.top_k)
    return KnowledgeSearchResponse(query=req.query, results=results)


@router.post("/knowledge/ingest", response_model=IngestResult)
def knowledge_ingest(
    payload: Any = Body(...), s: KnowledgeStore = Depends(get_store)
) -> IngestResult:
    """入库: 接收 List[KnowledgeRecordIn] 或 {"records": [...]}。"""
    records = _extract_records(payload)
    return s.ingest(records)


@router.post("/knowledge/upload", response_model=IngestResult)
async def knowledge_upload(
    file: UploadFile = File(...), s: KnowledgeStore = Depends(get_store)
) -> IngestResult:
    """上传 .json/.csv 文件入库。"""
    filename = (file.filename or "").lower()
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解码失败: {e}")

    if filename.endswith(".json"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")
        records = _extract_records(payload)
    elif filename.endswith(".csv"):
        records = _parse_csv(text)
    else:
        # 兜底: 尝试 json, 再尝试 csv
        try:
            records = _extract_records(json.loads(text))
        except Exception:
            records = _parse_csv(text)

    return s.ingest(records)


@router.get("/knowledge/stats", response_model=KnowledgeStats)
def knowledge_stats(s: KnowledgeStore = Depends(get_store)) -> KnowledgeStats:
    return s.stats()


@router.get("/knowledge/list")
def knowledge_list(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    s: KnowledgeStore = Depends(get_store),
) -> dict:
    total = s.count()
    items = s.list_all(limit=limit, offset=offset)
    return {"total": total, "items": items}


@router.delete("/knowledge/{record_id}")
def knowledge_delete(
    record_id: str, s: KnowledgeStore = Depends(get_store)
) -> dict:
    ok = s.delete(record_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"记录不存在: {record_id}")
    return {"deleted": record_id}


@router.delete("/knowledge")
def knowledge_clear(
    confirm: bool = Query(False, description="需 confirm=true 才允许清空"),
    s: KnowledgeStore = Depends(get_store),
) -> dict:
    if not confirm:
        raise HTTPException(status_code=400, detail="请携带 ?confirm=true 以确认清空知识库")
    n = s.clear()
    return {"cleared": n}


# ============ 健康/设置 ============
@router.get("/health", response_model=HealthResponse)
def health(s: KnowledgeStore = Depends(get_store)) -> HealthResponse:
    llm_ok, _ = llm_client.health()
    codehub_ok, _ = codehub_client.health()
    kb_count = s.count()
    return HealthResponse(
        status="ok",
        llm_configured=_settings.llm_configured,
        codehub_configured=_settings.codehub_configured,
        codehub_mock=_settings.codehub_mock,
        embed_provider=_settings.embed_provider,
        kb_count=kb_count,
        version="0.1.0",
    )


@router.post("/settings/test/llm", response_model=TestResult)
def test_llm(c: LLMClient = Depends(get_llm)) -> TestResult:
    ok, msg = c.health()
    return TestResult(ok=ok, message=msg)


@router.post("/settings/test/codehub", response_model=TestResult)
def test_codehub(c: CodeHubClient = Depends(get_codehub)) -> TestResult:
    ok, msg = c.health()
    return TestResult(ok=ok, message=msg)


@router.post("/settings/test/embedding", response_model=TestResult)
def test_embedding(e: EmbeddingClient = Depends(get_embed)) -> TestResult:
    ok, msg = e.health()
    return TestResult(ok=ok, message=msg)


# ============ 解析工具 ============
def _extract_records(payload: Any) -> List[KnowledgeRecordIn]:
    """从 List 或 {"records":[...]} 中提取 KnowledgeRecordIn。"""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("records") or payload.get("data") or []
        if isinstance(items, dict):
            items = [items]
    else:
        items = []

    records: List[KnowledgeRecordIn] = []
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        root_cause = (it.get("root_cause") or it.get("rootcause") or it.get("rootCause") or "").strip()
        if not title and not root_cause:
            continue
        records.append(
            KnowledgeRecordIn(
                title=title or root_cause,
                summary=it.get("summary"),
                root_cause=root_cause or title,
                verification=it.get("verification"),
                code_snippet=it.get("code_snippet") or it.get("code") or it.get("snippet"),
                code_path=it.get("code_path") or it.get("path"),
                language=it.get("language") or it.get("lang"),
                tags=_parse_tags(it.get("tags")),
                severity=it.get("severity"),
                product=it.get("product"),
                component=it.get("component"),
                source_url=it.get("source_url") or it.get("url"),
                raw=it.get("raw"),
            )
        )
    return records


def _parse_tags(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        # 分号或逗号分隔
        parts = val.replace(";", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    return [str(val)]


def _parse_csv(text: str) -> List[KnowledgeRecordIn]:
    """CSV 字段映射入库。"""
    reader = csv.DictReader(io.StringIO(text))
    items: List[dict] = []
    for row in reader:
        # 标准化键(去空白)
        norm = {k.strip() if k else k: v for k, v in row.items()}
        items.append(norm)
    return _extract_records(items)
