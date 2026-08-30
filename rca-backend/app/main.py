from __future__ import annotations
import asyncio
import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .mock_data import SAMPLE_TICKETS
from .models import AnalyzeRequest, AnalysisReport, KBImportRequest
from .opencode_adapter import OpenCodeAdapter
from .pipeline import Pipeline
from .retriever import Retriever

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RCA Command - 问题单智能根因分析", version="1.0.0")

retriever = Retriever(path=str(DATA_DIR / "chroma"))
retriever.seed_if_empty(SAMPLE_TICKETS)
opencode = OpenCodeAdapter(
    binary=os.environ.get("OPENCODE_BINARY", "opencode"),
    model=os.environ.get("OPENCODE_MODEL"),
)
pipeline = Pipeline(retriever=retriever, opencode=opencode, repos_dir=str(DATA_DIR / "repos"))

tasks: dict[str, dict] = {}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "opencode_available": opencode.available,
        "kb_count": retriever.count(),
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    if not req.ticket_url or not req.repo_url:
        raise HTTPException(status_code=400, detail="ticket_url 与 repo_url 必填")
    task_id = uuid.uuid4().hex[:12]
    queue: asyncio.Queue = asyncio.Queue()
    tasks[task_id] = {"queue": queue, "report": None, "status": "running"}

    async def _run():
        try:
            async for evt in pipeline.run_async(req):
                await queue.put(evt)
                if evt.get("type") == "report":
                    tasks[task_id]["report"] = AnalysisReport(**evt["data"])
                    tasks[task_id]["status"] = "done"
        except Exception as e:
            await queue.put({"type": "error", "data": {"message": str(e)}})
            tasks[task_id]["status"] = "error"

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.get("/api/analyze/{task_id}/stream")
async def analyze_stream(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="task not found")

    async def event_gen():
        q = tasks[task_id]["queue"]
        while True:
            evt = await q.get()
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") in ("report", "error"):
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


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


@app.get("/")
async def index():
    html = FRONTEND_DIR / "rca-command.html"
    if html.exists():
        return FileResponse(html)
    return {"message": "RCA Command API running", "opencode": opencode.available, "kb": retriever.count()}


app.mount("/static", StaticFiles(directory=str(BASE_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
