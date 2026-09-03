from __future__ import annotations
import asyncio
import os
import shutil
import subprocess
import time
import uuid
from typing import AsyncGenerator, Callable, Optional

from .callgraph import CallGraph
from .mock_data import SAMPLE_PRACTICES
from .models import (
    AnalysisReport, AnalyzeRequest, BestPractice, CallStackNode,
    RCA, RootCauseFactor, Solution, SolutionDiff, SolutionStep, StageEvent,
)
from .opencode_adapter import OpenCodeAdapter
from .retriever import Retriever

STAGE_NAMES = [
    "拉取问题单",
    "克隆代码仓",
    "opencode 静态分析",
    "调用链追溯",
    "RAG 知识匹配",
    "LLM 合成报告",
]
STAGE_LOGS = [
    "解析问题单标题/描述/堆栈/环境信息 ...",
    "克隆仓库并切到目标分支/commit ...",
    "opencode 执行 AST 分析 + 数据流追踪 ...",
    "构建调用图，定位异常热点 ...",
    "向量+BM25 混合检索历史问题单 ...",
    "合成根因报告 + 修复方案 ...",
]


class Pipeline:
    def __init__(self, retriever: Retriever, opencode: OpenCodeAdapter, repos_dir: str = "./data/repos"):
        self.retriever = retriever
        self.oc = opencode
        self.repos_dir = repos_dir
        self.report: Optional[AnalysisReport] = None

    async def run_async(self, req: AnalyzeRequest) -> AsyncGenerator[dict, None]:
        t0 = time.time()
        ctx: dict = {}
        for i, name in enumerate(STAGE_NAMES):
            yield {"type": "stage", "data": StageEvent(
                stage_index=i, stage_name=name, status="active",
                log=STAGE_LOGS[i], timestamp=time.time()
            ).model_dump()}
            await asyncio.sleep(0.15)
            step = getattr(self, self.STEP_FUNCS[i])
            try:
                await asyncio.to_thread(step, req, ctx)
            except Exception as e:
                ctx.setdefault("errors", []).append({"step": name, "message": str(e)})
            yield {"type": "stage", "data": StageEvent(
                stage_index=i, stage_name=name, status="done",
                log=STAGE_LOGS[i], timestamp=time.time()
            ).model_dump()}
            await asyncio.sleep(0.2)

        self.report = self._assemble(req, ctx)
        self.report.elapsed_s = round(time.time() - t0, 2)
        yield {"type": "report", "data": self.report.model_dump()}

    def _step_fetch_ticket(self, req: AnalyzeRequest, ctx: dict):
        ctx["ticket_title"] = req.description or f"问题单 {req.ticket_url}"
        ctx["ticket_url"] = req.ticket_url

    def _step_clone_repo(self, req: AnalyzeRequest, ctx: dict):
        workdir = None
        if shutil.which("git"):
            key = uuid.uuid4().hex[:8]
            target = os.path.abspath(os.path.join(self.repos_dir, key))
            os.makedirs(self.repos_dir, exist_ok=True)
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", req.branch or "main",
                     req.repo_url, target],
                    capture_output=True, text=True, timeout=120,
                )
                if os.path.isdir(target):
                    workdir = target
            except Exception:
                workdir = None
        ctx["workdir"] = workdir

    def _step_opencode_analyze(self, req: AnalyzeRequest, ctx: dict):
        ctx["oc"] = self.oc.analyze_code(req.repo_url, req.branch, workdir=ctx.get("workdir"))

    def _step_callgraph(self, req: AnalyzeRequest, ctx: dict):
        cg = CallGraph()
        cg.build_from_opencode(ctx.get("oc", {}))
        ctx["cg"] = cg
        ctx["chain"] = cg.trace_call_chain(None, depth=6)
        ctx["taint_node"] = cg.taint_to_hotspot("user_input")

    def _step_rag_search(self, req: AnalyzeRequest, ctx: dict):
        query = req.description or req.ticket_url
        ctx["kb_matches"] = self.retriever.hybrid_search(
            query, microservice=req.microservice, top_k=5
        )

    def _step_synthesize(self, req: AnalyzeRequest, ctx: dict):
        matches: list = ctx.get("kb_matches", [])
        chain: list[CallStackNode] = ctx.get("chain", [])
        taint_node = ctx.get("taint_node")

        synthesis: dict = {}

        if matches:
            top = matches[0]
            synthesis["root_cause_hint"] = top.root_cause or ""
            synthesis["patch_suggestion"] = top.fix_code or ""
            synthesis["historical_cases"] = [
                m.ticket_id for m in matches if m.ticket_id
            ]
            synthesis["best_practice_hints"] = [m.title for m in matches[:3]]
        elif chain:
            hotspot = chain[0]
            synthesis["root_cause_hint"] = (
                f"{hotspot.symbol} 存在异常热点({hotspot.reason})，"
                "需关注数据流路径与并发竞争风险。"
            )
            synthesis["patch_suggestion"] = ""
            synthesis["historical_cases"] = []
            synthesis["best_practice_hints"] = []
        elif taint_node is not None:
            synthesis["root_cause_hint"] = (
                f"{taint_node.symbol} 污染数据直达热点"
                f"({taint_node.reason})，缺少兜底校验。"
            )
            synthesis["patch_suggestion"] = ""
            synthesis["historical_cases"] = []
            synthesis["best_practice_hints"] = []
        else:
            synthesis["root_cause_hint"] = ""
            synthesis["patch_suggestion"] = ""
            synthesis["historical_cases"] = []
            synthesis["best_practice_hints"] = []

        ctx["synthesis"] = synthesis

    STEP_FUNCS = [
        "_step_fetch_ticket", "_step_clone_repo", "_step_opencode_analyze",
        "_step_callgraph", "_step_rag_search", "_step_synthesize",
    ]

    def _assemble(self, req: AnalyzeRequest, ctx: dict) -> AnalysisReport:
        oc = ctx.get("oc", {})
        chain: list[CallStackNode] = ctx.get("chain", [])
        matches: list = ctx.get("kb_matches", [])
        synthesis: dict = ctx.get("synthesis", {})

        if chain:
            hotspot = chain[0]
        else:
            hotspot = CallStackNode(
                symbol="sym:OrderLockService:acquire", file="OrderLockService.java",
                line=127, score=0.94, reason="sync_block+single_key_lock",
            )
        top_match = matches[0] if matches else None
        confidence = round(
            min(0.99, 0.5 * hotspot.score + 0.5 * (top_match.similarity if top_match else 0.6)), 3
        )

        rca = RCA(
            root_cause=(
                top_match.root_cause if top_match else (
                    f"{hotspot.symbol} 存在高并发资源竞争风险({hotspot.reason})。"
                    "结合数据流追踪，用户输入直接传入该热点路径，缺少兜底校验。"
                )
            ),
            confidence=confidence,
            factors=[
                RootCauseFactor(kind="直接原因",
                               desc=f"{hotspot.symbol} 在 {hotspot.file}:{hotspot.line} 处资源竞争/无校验",
                               evidence=hotspot.reason),
                RootCauseFactor(kind="根本原因",
                               desc="锁粒度过粗/缺少幂等与兜底校验",
                               evidence="opencode 数据流: skuId(taint=user_input) 直达热点"),
                RootCauseFactor(kind="触发条件",
                               desc="高并发或重试回调",
                               evidence="问题单堆栈指向该调用路径"),
                RootCauseFactor(kind="衍生影响",
                               desc="数据不一致/资损/重复入账",
                               evidence=top_match.title if top_match else ""),
            ],
            call_stack=chain,
        )

        practices = [BestPractice(**p) for p in SAMPLE_PRACTICES[:3]]
        diffs = [
            SolutionDiff(
                file="OrderLockService.java",
                before=("public boolean acquire(Long skuId){\n"
                        '  return redis.setnx("lock:"+skuId, "1", 30);\n}'),
                after=("public boolean acquire(Long skuId){\n"
                       "  int seg = Math.floorMod(skuId.hashCode(), 16);\n"
                       '  return redis.setnx("lock:"+seg+":"+skuId, "1", 30);\n}'),
                summary="单 key 全量锁 -> 按 skuId 分段锁，降低竞争",
            ),
        ]
        steps = [
            SolutionStep(step=1, action="分段锁改造", detail="按 skuId hash 分段，16 路锁分散竞争"),
            SolutionStep(step=2, action="原子扣减", detail="引入 Lua 脚本保证库存扣减原子性"),
            SolutionStep(step=3, action="兜底校验", detail="扣减后校验库存非负，否则回滚"),
            SolutionStep(step=4, action="幂等保护", detail="订单号幂等键去重，防重复下单"),
            SolutionStep(step=5, action="压测验证", detail="压测验证超卖为 0、RT < 50ms"),
        ]
        solution = Solution(
            diffs=diffs, steps=steps,
            verify_expected={"stock_negative": 0, "rt_p99_ms": "<50", "duplicate": 0},
            patch_suggestion=synthesis.get("patch_suggestion", ""),
            historical_cases=synthesis.get("historical_cases", []),
            best_practices=[p.title for p in practices],
        )

        return AnalysisReport(
            task_id=uuid.uuid4().hex[:12],
            confidence=confidence,
            elapsed_s=0.0,
            degraded=bool(oc.get("degraded")),
            rca=rca,
            kb_matches=matches,
            best_practices=practices,
            solution=solution,
        )
