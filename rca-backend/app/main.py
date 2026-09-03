from __future__ import annotations
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .mock_data import SAMPLE_TICKETS
from .models import (
    AnalyzeRequest, AnalyzeResponse, AnalysisReport, ConfirmRequest, KBImportRequest, KBImportItem, RCAState,
    Code2CnRequest, CodeOutline, AstKg, AstKgEntity, AstKgRelationship,
)
from .opencode_adapter import OpenCodeAdapter
from .pipeline import Pipeline
from .retriever import Retriever
from .engine import engine, RCAEngine
from .agents import AgentA1, AgentA5
from .runtime_mode import get_current_mode, component_status as _component_status
from .code2cn import code2cn
from .codegraph import CodeGraph
from .lightrag_adapter import lightrag, intent_to_mode

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RCA Command - 问题单智能根因分析", version="3.0.0")

retriever = Retriever(path=str(DATA_DIR / "chroma"))
retriever.seed_if_empty(SAMPLE_TICKETS)
opencode = OpenCodeAdapter(
    binary=os.environ.get("OPENCODE_BINARY", "opencode"),
    model=os.environ.get("OPENCODE_MODEL"),
)
pipeline = Pipeline(retriever=retriever, opencode=opencode, repos_dir=str(DATA_DIR / "repos"))

engine.a1 = AgentA1(opencode=opencode)
engine.a5 = AgentA5(retriever=retriever, opencode=opencode)

tasks: dict[str, dict] = {}
v3_tasks: dict[str, dict] = {}


# ============================================================================
# V2 端点（向后兼容）
# ============================================================================

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "opencode_available": opencode.available,
        "kb_count": retriever.count(),
        "version": "3.0.0",
    }


@app.get("/api/v1/health")
async def v1_health():
    """V3 健康检查：返回各组件状态与运行模式。"""
    components = _component_status()
    all_up = all(v in ("up", "mock") for v in components.values())
    return {
        "status": "up" if all_up else "degraded",
        "components": {
            "postgres": {"status": "up", "latency_ms": 0},
            "redis": {"status": "up", "latency_ms": 0},
            "lightrag": {"status": components.get("lightrag", "down")},
            "codegraph": {"status": components.get("codegraph", "down")},
            "llm": {"status": components.get("llm", "down"), "provider": get_current_mode()},
        },
        "runtime_mode": get_current_mode(),
        "version": "3.0.0",
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    if not req.bug_link and not req.bug_desc:
        raise HTTPException(status_code=400, detail="bug_link 或 bug_desc 必填")
    task_id_ = _generate_task_id()
    queue: asyncio.Queue = asyncio.Queue()
    tasks[task_id_] = {"queue": queue, "report": None, "status": "running"}

    async def _run():
        try:
            async for evt in pipeline.run_async(req):
                await queue.put(evt)
                if evt.get("type") == "report":
                    tasks[task_id_]["report"] = AnalysisReport(**evt["data"])
                    tasks[task_id_]["status"] = "done"
        except Exception as e:
            await queue.put({"type": "error", "data": {"message": str(e)}})
            tasks[task_id_]["status"] = "error"

    asyncio.create_task(_run())
    return {"task_id": task_id_}


@app.get("/api/analyze/{task_id}/stream")
async def analyze_stream(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="task not found")
    return _sse_response(tasks[task_id]["queue"])


@app.get("/api/analyze/{task_id}")
async def get_report(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="task not found")
    report = tasks[task_id].get("report")
    if not report:
        raise HTTPException(status_code=409, detail="analysis not finished")
    return report


@app.post("/api/kb/import")
async def kb_import(req: KBImportRequest):
    n = retriever.import_tickets(req.items)
    return {"imported": n, "total": retriever.count()}


@app.get("/api/kb/count")
async def kb_count():
    return {"count": retriever.count()}


class YunjieImportRequest(BaseModel):
    ticket_refs: list[str]


@app.post("/api/v1/yunjie/import")
async def yunjie_import(req: YunjieImportRequest):
    raw = opencode.fetch_yunjie_tickets(req.ticket_refs)
    items: list[KBImportItem] = []
    for d in raw:
        if not d.get("ticket_id"):
            continue
        try:
            items.append(KBImportItem(**d))
        except Exception:
            continue
    n = retriever.import_tickets(items)
    for it in items:
        text = (
            f"问题单 {it.ticket_id}: {it.title}\n"
            f"描述: {it.description}\n"
            f"根因: {it.root_cause}\n"
            f"修复: {it.fix_code}"
        )
        try:
            await lightrag.ainsert(text, ids=f"yunjie:{it.ticket_id}")
        except Exception:
            pass
    return {"imported": n, "total": retriever.count(), "lightrag_degraded": not lightrag.available}


class KbDeleteRequest(BaseModel):
    ids: list[str]


@app.get("/api/kb/tickets")
async def kb_tickets(limit: int = 50, q: Optional[str] = None):
    items = retriever.list_tickets(limit=limit, q=q)
    return {"items": items, "total": len(items), "kb_count": retriever.count()}


@app.delete("/api/kb/tickets")
async def kb_delete_tickets(req: KbDeleteRequest):
    n = retriever.delete_tickets(req.ids)
    return {"deleted": n, "total": retriever.count()}


# ============================================================================
# V3 端点（5-Agent 智能引擎）
# ============================================================================

@app.post("/api/v1/rca/analyze")
async def v3_analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    if not req.bug_link and not req.bug_desc:
        raise HTTPException(status_code=400, detail="bug_link 或 bug_desc 必填")
    task_id_ = engine.generate_task_id()
    state = RCAState(
        task_id=task_id_,
        runtime_mode=req.runtime_mode,
        bug_info={
            "bug_id": req.bug_id,
            "title": req.symptom,
            "description": req.bug_desc,
            "error_type": req.error_type or "",
            "link": req.bug_link,
        },
    )
    queue: asyncio.Queue = asyncio.Queue()
    v3_tasks[task_id_] = {"queue": queue, "state": None, "status": "running"}

    async def _run():
        try:
            result = await asyncio.to_thread(engine.run_sequential, state)
            v3_tasks[task_id_]["state"] = result
            v3_tasks[task_id_]["status"] = "done" if result.stage.status == "completed" else "failed"
            await queue.put({"type": "final", "data": _state_summary(result)})
        except Exception as e:
            await queue.put({"type": "error", "data": {"message": str(e)}})
            v3_tasks[task_id_]["status"] = "error"

    asyncio.create_task(_run())
    return AnalyzeResponse(task_id=task_id_, status="queued", runtime_mode=req.runtime_mode)


@app.get("/api/v1/rca/tasks")
async def v3_list_tasks():
    return {"tasks": list(v3_tasks.keys()), "statuses": {k: v.get("status", "unknown") for k, v in v3_tasks.items()}}


@app.get("/api/v1/rca/{task_id}/stream")
async def v3_analyze_stream(task_id: str):
    if task_id not in v3_tasks:
        raise HTTPException(status_code=404, detail="task not found")

    async def event_gen():
        q = v3_tasks[task_id]["queue"]
        while True:
            evt = await q.get()
            if evt is None:
                break
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") in ("final", "error"):
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.get("/api/v1/rca/{task_id}")
async def v3_get_result(task_id: str):
    if task_id not in v3_tasks:
        raise HTTPException(status_code=404, detail="task not found")
    info = v3_tasks[task_id]
    state = info.get("state")
    if not state:
        raise HTTPException(status_code=409, detail="analysis not finished")
    return _state_summary(state)


@app.get("/api/v1/rca/{task_id}/state")
async def v3_get_state(task_id: str):
    state = engine.get_state(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _state_summary(state)


@app.post("/api/v1/rca/{task_id}/confirm")
async def v3_confirm(task_id: str, decision: ConfirmRequest):
    record = v3_tasks.get(task_id, {})
    record["status"] = "confirmed"
    try:
        result = engine.resume(task_id, decision)
        v3_tasks[task_id] = {"state": result, "status": "done" if result.stage.status == "completed" else "rejected", "queue": asyncio.Queue()}
        return _state_summary(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/rca/{task_id}/resume")
async def v3_resume(task_id: str):
    try:
        result = engine.resume_from_checkpoint(task_id)
        v3_tasks[task_id] = {"state": result, "status": "done" if result.stage.status == "completed" else "failed", "queue": asyncio.Queue()}
        return _state_summary(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# 首页
# ============================================================================

@app.get("/")
async def index():
    html = FRONTEND_DIR / "rca-command.html"
    if html.exists():
        return FileResponse(html)
    return {"message": "RCA Command API v3 running", "opencode": opencode.available, "kb": retriever.count()}


app.mount("/static", StaticFiles(directory=str(BASE_DIR)), name="static")


# ============================================================================
# 辅助函数
# ============================================================================

def _generate_task_id() -> str:
    return f"v2-{int(time.time())}-{os.urandom(4).hex()}"


def _sse_response(queue: asyncio.Queue):
    async def event_gen():
        while True:
            evt = await queue.get()
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") in ("report", "error"):
                break
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


def _state_summary(state: RCAState) -> dict:
    return {
        "task_id": state.task_id,
        "stage": state.stage.model_dump(),
        "gate_status": state.gate_status.model_dump(),
        "top3": [r.model_dump() for r in state.top3],
        "solution": state.solution.model_dump() if state.solution else {},
        "symptoms": state.symptoms,
        "error_type": state.error_type,
        "suspect_services": state.suspect_services,
        "degraded": state.degraded,
        "runtime_mode": state.runtime_mode,
    }


# ============================================================================
# Spec 1: code2cn REST API
# ============================================================================

@app.post("/api/v1/code2cn/generate", response_model=CodeOutline)
async def code2cn_generate(req: Code2CnRequest):
    """生成函数级中文大纲。"""
    outline = code2cn.generate(req)
    return outline


@app.get("/api/v1/code2cn/outline/{symbol}", response_model=CodeOutline)
async def code2cn_outline(symbol: str):
    """按符号获取缓存的中文大纲（MCP 工具等价）。"""
    for outline in code2cn._cache.values():
        if outline.symbol == symbol:
            return outline
    raise HTTPException(status_code=404, detail=f"符号 {symbol} 无缓存大纲，请先 POST generate")


# ============================================================================
# Spec 2: CodeGraph REST API
# ============================================================================

_codegraph = CodeGraph()
_codegraph.init_schema()
_codegraph.seed_mock_data()

@app.get("/api/v1/codegraph/node/{symbol}")
async def codegraph_node(symbol: str):
    """获取函数节点信息。"""
    node = _codegraph._get_node_by_symbol(symbol)
    if node is None:
        raise HTTPException(status_code=404, detail=f"符号 {symbol} 不在图谱中")
    return node


@app.get("/api/v1/codegraph/callers/{symbol}")
async def codegraph_callers(symbol: str, depth: int = 2):
    """获取调用者（谁调用了 symbol）。"""
    resp = _codegraph.callers(symbol, depth=depth)
    if resp is None:
        raise HTTPException(status_code=404, detail=f"符号 {symbol} 不在图谱中")
    return resp.model_dump()


@app.get("/api/v1/codegraph/callees/{symbol}")
async def codegraph_callees(symbol: str):
    """获取被调用者（symbol 调用了谁）。"""
    resp = _codegraph.callees(symbol)
    if resp is None:
        raise HTTPException(status_code=404, detail=f"符号 {symbol} 不在图谱中")
    return resp.model_dump()


@app.get("/api/v1/codegraph/explore/{symbol}")
async def codegraph_explore(symbol: str, hops: int = 2):
    """探索符号邻域。"""
    resp = _codegraph.explore(symbol, hops=hops)
    if resp is None:
        raise HTTPException(status_code=404, detail=f"符号 {symbol} 不在图谱中")
    return resp.model_dump()


# ============================================================================
# Spec 3: LightRAG REST API
# ============================================================================

@app.post("/api/v1/rag/query")
async def rag_query(query: str, intent: str = "history", top_k: int = 10):
    """三路检索路由查询。

    intent: history(历史经验匹配) | propagation(根因传播追溯) | architecture(全局架构理解)
    """
    mode = intent_to_mode(intent)
    result = await lightrag.aquery(query, mode=mode, top_k=top_k)
    return result.model_dump()


@app.post("/api/v1/rag/insert")
async def rag_insert(text: str, ids: Optional[str] = None):
    """批量索引文本到 LightRAG 文本域。"""
    ok = await lightrag.ainsert(text, ids=ids)
    return {"success": ok, "degraded": not lightrag.available}


@app.post("/api/v1/rag/insert_kg")
async def rag_insert_kg(ast_kg: AstKg):
    """注入 CodeGraph 调用图到 LightRAG 结构域。"""
    ok = await lightrag.ainsert_custom_kg(ast_kg)
    return {"success": ok, "degraded": not lightrag.available}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)