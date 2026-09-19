from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .mock_data import SAMPLE_TICKETS
from .models import (
    AnalyzeRequest, AnalyzeRequestV2, AnalyzeResponse, AnalysisReport, ConfirmRequest, HilDecision, KBImportRequest, KBImportItem, RCAState,
)
from .opencode_adapter import OpenCodeAdapter
from .pipeline import Pipeline
from .retriever import Retriever
from .engine import engine, RCAEngine
from .opencode_serve_adapter import OpenCodeServeAdapter
from .config import config
from .runtime_mode import get_current_mode, component_status as _component_status
from .lightrag_adapter import lightrag

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

engine.set_serve_adapter(
    OpenCodeServeAdapter(
        base_url=config.opencode_serve.base_url,
        auth_token=config.opencode_serve.auth_token,
        timeout=config.opencode_serve.timeout,
    )
)

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

    v2_req = AnalyzeRequestV2(
        ticket_url=req.bug_link or "",
        repo_url=req.repo or "",
        branch=req.branch,
        microservice=req.suspect_service,
        description=req.bug_desc or None,
        depth=req.depth,
    )

    async def _run():
        try:
            async for evt in pipeline.run_async(v2_req):
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
    failed: list[str] = []
    for it in items:
        text = (
            f"问题单 {it.ticket_id}: {it.title}\n"
            f"描述: {it.description}\n"
            f"根因: {it.root_cause}\n"
            f"修复: {it.fix_code}"
        )
        try:
            await lightrag.ainsert(text, ids=f"yunjie:{it.ticket_id}")
        except Exception as e:
            logger.warning("yunjie lightrag.ainsert 失败: ticket_id=%s err=%s", it.ticket_id, e)
            failed.append(it.ticket_id)
    return {"imported": n, "total": retriever.count(), "lightrag_degraded": not lightrag.available, "lightrag_failed_ids": failed}


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
# V3 端点（opencode serve headless 引擎）
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
            "environment": {"repo_path": req.repo_path} if req.repo_path else {},
        },
    )
    queue: asyncio.Queue = asyncio.Queue()
    v3_tasks[task_id_] = {"queue": queue, "state": None, "status": "running"}

    async def _relay_events():
        """将 SSEEventBus 事件中继到 asyncio.Queue，实现实时进度透传。"""
        offset = 0
        while True:
            events, offset = await asyncio.to_thread(engine.drain_events, task_id_, offset)
            for evt in events:
                await queue.put({
                    "type": evt.get("event", "stage"),
                    "data": evt.get("data", {}),
                    "ts": evt.get("ts"),
                })
            if v3_tasks[task_id_]["status"] != "running":
                break
            await asyncio.sleep(0.1)

    async def _run():
        relay = asyncio.create_task(_relay_events())
        try:
            result = await asyncio.to_thread(engine.run_sequential, state)
            v3_tasks[task_id_]["state"] = result
            v3_tasks[task_id_]["status"] = "done" if result.stage.status == "completed" else "failed"
            await queue.put({"type": "final", "data": _state_summary(result)})
        except Exception as e:
            await queue.put({"type": "error", "data": {"message": str(e)}})
            v3_tasks[task_id_]["status"] = "error"
        finally:
            await relay
            await queue.put(None)

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
        hil = HilDecision(
            task_id=task_id,
            action=decision.action,
            confirmed_root_cause_id=decision.confirmed_root_cause_id,
            modified_top3=decision.modified_top3,
            feedback=decision.feedback,
        )
        result = await asyncio.to_thread(engine.resume, task_id, hil)
        v3_tasks[task_id] = {"state": result, "status": "done" if result.stage.status == "completed" else "rejected", "queue": asyncio.Queue()}
        return _state_summary(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/rca/{task_id}/resume")
async def v3_resume(task_id: str):
    try:
        result = await asyncio.to_thread(engine.resume_from_checkpoint, task_id)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)