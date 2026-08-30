from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import List, Optional

from app.config import Settings
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    CodeChange,
    CodeRef,
    DesignSolution,
    MatchedCase,
    MRSummary,
    RootCause,
    VerificationSuggestion,
)
from app.services.best_practice import BestPracticeExplorer
from app.services.codehub import CodeHubClient, MRContext
from app.services.llm import EmbeddingClient, LLMClient
from app.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)


class Analyzer:
    """根因分析编排器: 串联 CodeHub → LLM → 知识库检索 → 设计方案 → 验证建议。"""

    def __init__(
        self,
        settings: Settings,
        codehub: CodeHubClient,
        llm: LLMClient,
        embed: EmbeddingClient,
        store: KnowledgeStore,
    ):
        self.settings = settings
        self.codehub = codehub
        self.llm = llm
        self.embed = embed
        self.store = store
        # 最佳实践探索器(内部组合, 充分利用已注入的 settings/llm)
        self.bp_explorer = BestPracticeExplorer(settings, llm)

    # ============ 主编排 ============
    def analyze(self, req: AnalyzeRequest) -> AnalyzeResponse:
        warnings: List[str] = []
        t0 = time.time()

        # 1) 获取 MR 上下文
        ctx: Optional[MRContext] = None
        mr_summary: Optional[MRSummary] = None
        try:
            ctx = self.codehub.fetch_mr_context(
                mr_url=req.mr_url,
                repo=req.repo,
                branch=req.branch,
                pasted_content=req.pasted_content,
            )
            mr_summary = self._build_mr_summary(ctx)
        except Exception as e:
            warnings.append(f"获取 MR 上下文失败: {e}")

        # 2) 构建 LLM 上下文文本
        ctx_text = self._build_context_text(ctx) if ctx else ""

        # 3) 根因分析
        root_cause: Optional[RootCause] = None
        if not self.llm.configured():
            warnings.append("LLM 未配置, 根因/设计方案/验证建议为降级输出")
            root_cause = RootCause(summary="LLM 未配置，仅展示 MR 信息")
        else:
            try:
                root_cause = self._analyze_root_cause(ctx_text, ctx)
            except Exception as e:
                warnings.append(f"根因分析失败: {e}")
                root_cause = RootCause(summary="根因分析失败，仅展示 MR 信息")

        # 4) 知识库检索
        matched_cases: List[MatchedCase] = []
        try:
            query = " ".join(
                p for p in [root_cause.summary if root_cause else "", mr_summary.title if mr_summary else ""]
                if p
            )
            if query:
                matched_cases = self.store.search(query, top_k=5)
        except Exception as e:
            warnings.append(f"知识库检索失败: {e}")

        # 5) 设计方案
        design_solution: Optional[DesignSolution] = None
        if self.llm.configured() and ctx:
            try:
                design_solution = self._build_design_solution(root_cause, matched_cases, ctx)
            except Exception as e:
                warnings.append(f"设计方案生成失败: {e}")
        elif not self.llm.configured():
            pass  # 已在根因处降级提示

        # 6) 验证建议
        verification: Optional[VerificationSuggestion] = None
        if self.llm.configured() and ctx:
            try:
                verification = self._build_verification(root_cause, ctx)
            except Exception as e:
                warnings.append(f"验证建议生成失败: {e}")

        # 7) 最佳实践(降级, 永不阻断)
        best_practices = []
        try:
            if self.llm.configured() and root_cause:
                best_practices = self.bp_explorer.explore(root_cause.summary)
        except Exception as e:
            warnings.append(f"最佳实践探索失败: {e}")

        # 8) 组装响应
        elapsed_ms = int((time.time() - t0) * 1000)
        if warnings:
            status = "partial"
        else:
            status = "ok"

        return AnalyzeResponse(
            task_id=str(uuid.uuid4()),
            status=status,
            warnings=warnings,
            mr=mr_summary,
            root_cause=root_cause,
            matched_cases=matched_cases,
            best_practices=best_practices,
            design_solution=design_solution,
            verification=verification,
            elapsed_ms=elapsed_ms,
            created_at=datetime.utcnow(),
        )

    # ============ MR 摘要 ============
    @staticmethod
    def _build_mr_summary(ctx: MRContext) -> MRSummary:
        diff = ctx.combined_diff()
        return MRSummary(
            mr_iid=ctx.mr_iid or None,
            title=ctx.title or None,
            source_branch=ctx.source_branch or None,
            target_branch=ctx.target_branch or None,
            author=ctx.author or None,
            state=ctx.state or None,
            changed_files=len(ctx.files),
            description=ctx.description or None,
            diff_stats=Analyzer._diff_stats(diff),
            changed_file_paths=[f.new_path or f.old_path for f in ctx.files],
            diff=diff,
        )

    @staticmethod
    def _diff_stats(diff: str) -> dict:
        added = deleted = 0
        for line in (diff or "").splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                deleted += 1
        return {"added": added, "deleted": deleted}

    # ============ LLM 上下文构建 ============
    @staticmethod
    def _build_context_text(ctx: MRContext) -> str:
        depth_truncate = {"quick": 4000, "standard": 10000, "deep": 16000}.get("standard", 10000)
        diff = ctx.combined_diff(limit=depth_truncate)
        notes = "\n".join(f"- {n}" for n in ctx.notes) if ctx.notes else "(无)"
        file_contents = ""
        if ctx.file_contents:
            parts = []
            for path, content in ctx.file_contents.items():
                parts.append(f"### 文件: {path}\n```\n{content[:3000]}\n```")
            file_contents = "\n\n".join(parts)
        else:
            file_contents = "(无完整文件内容)"
        return (
            f"## MR 标题\n{ctx.title or '(无)'}\n\n"
            f"## MR 描述\n{ctx.description or '(无)'}\n\n"
            f"## 变更 Diff\n{diff or '(无)'}\n\n"
            f"## 评审/讨论评论\n{notes}\n\n"
            f"## 变更文件全文\n{file_contents}"
        )

    # ============ 根因分析 ============
    def _analyze_root_cause(self, ctx_text: str, ctx: Optional[MRContext]) -> RootCause:
        system = (
            "你是一位资深代码质量工程师与根因分析专家。请基于提供的合并请求(MR)上下文,"
            "深入分析代码变更背后的问题根因。结论必须基于 diff 中的客观证据,"
            "不要臆测未给出的信息。"
        )
        user = (
            f"以下是待分析的 MR 上下文:\n\n{ctx_text}\n\n"
            "请输出 JSON, 字段如下:\n"
            "{\n"
            '  "summary": "根因一句话总结",\n'
            '  "category": "类别(空值/并发/资源泄漏/逻辑错误/安全/性能/兼容性/其他)",\n'
            '  "contributing_factors": ["促成该问题的因素1", "因素2"],\n'
            '  "evidence": [{"file":"文件路径","lines":"行号或范围","snippet":"关键代码片段","explanation":"该证据说明"}],\n'
            '  "severity": "严重程度(critical/high/medium/low)"\n'
            "}\n"
            "请确保输出为合法 JSON。"
        )
        data = self.llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return RootCause(
            summary=str(data.get("summary", "")) or "未能生成根因总结",
            category=data.get("category"),
            contributing_factors=_as_list(data.get("contributing_factors")),
            evidence=[_as_code_ref(e) for e in _as_list(data.get("evidence"))],
            severity=data.get("severity"),
        )

    # ============ 设计方案 ============
    def _build_design_solution(
        self,
        root_cause: Optional[RootCause],
        matched_cases: List[MatchedCase],
        ctx: MRContext,
    ) -> DesignSolution:
        rc_text = root_cause.summary if root_cause else "(无根因)"
        cases_text = ""
        if matched_cases:
            parts = []
            for i, c in enumerate(matched_cases, 1):
                parts.append(
                    f"案例{i}: {c.title}\n根因: {c.root_cause}\n代码: {c.code_snippet or '(无)'}"
                )
            cases_text = "\n\n".join(parts)
        else:
            cases_text = "(未检索到相似历史案例)"

        system = (
            "你是一位资深软件架构师。请基于问题根因、相似历史案例与 MR diff,"
            "给出可落地的修复设计方案, 并说明权衡与预防措施。"
        )
        user = (
            f"## 根因\n{rc_text}\n\n"
            f"## 相似历史案例\n{cases_text}\n\n"
            f"## MR Diff\n{ctx.combined_diff(limit=8000)}\n\n"
            "请输出 JSON:\n"
            "{\n"
            '  "approach": "解决方案总体思路",\n'
            '  "rationale": "为何这样设计",\n'
            '  "code_changes": [{"file":"文件","change_type":"新增/修改/删除","description":"改动描述","patch":"可选补丁片段"}],\n'
            '  "tradeoffs": ["权衡1"],\n'
            '  "prevention": ["预防措施1"]\n'
            "}\n"
            "请确保输出为合法 JSON。"
        )
        data = self.llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return DesignSolution(
            approach=str(data.get("approach", "")) or "(未生成)",
            rationale=str(data.get("rationale", "")) or "(未生成)",
            code_changes=[_as_code_change(c) for c in _as_list(data.get("code_changes"))],
            tradeoffs=_as_list(data.get("tradeoffs")),
            prevention=_as_list(data.get("prevention")),
        )

    # ============ 验证建议 ============
    def _build_verification(
        self, root_cause: Optional[RootCause], ctx: MRContext
    ) -> VerificationSuggestion:
        rc_text = root_cause.summary if root_cause else "(无根因)"
        system = "你是一位资深测试与质量保障专家。请针对该根因给出验证与回归测试建议。"
        user = (
            f"## 根因\n{rc_text}\n\n"
            f"## MR 标题\n{ctx.title}\n\n"
            f"## 关键 Diff\n{ctx.combined_diff(limit=4000)}\n\n"
            "请输出 JSON:\n"
            "{\n"
            '  "steps": ["验证步骤1"],\n'
            '  "test_cases": ["建议的测试用例1"],\n'
            '  "risks": ["潜在风险1"]\n'
            "}\n"
            "请确保输出为合法 JSON。"
        )
        data = self.llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return VerificationSuggestion(
            steps=_as_list(data.get("steps")),
            test_cases=_as_list(data.get("test_cases")),
            risks=_as_list(data.get("risks")),
        )


# ============ 字段容错工具 ============
def _as_list(val) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x is not None]
    if isinstance(val, str):
        return [val] if val.strip() else []
    return [str(val)]


def _as_code_ref(val) -> CodeRef:
    if not isinstance(val, dict):
        return CodeRef(file=str(val) if val else "")
    return CodeRef(
        file=str(val.get("file", "")),
        lines=val.get("lines"),
        snippet=val.get("snippet"),
        explanation=val.get("explanation"),
    )


def _as_code_change(val) -> CodeChange:
    if not isinstance(val, dict):
        return CodeChange(file="", description=str(val) if val else "")
    return CodeChange(
        file=str(val.get("file", "")),
        change_type=val.get("change_type"),
        description=str(val.get("description", "")) or "",
        patch=val.get("patch"),
    )
