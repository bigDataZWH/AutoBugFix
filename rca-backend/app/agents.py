from __future__ import annotations

from typing import Any, Optional

from .config import config
from .models import (
    A1Output, AnomalyPath, BugInfo, RootCause, Solution,
    SuspectFunction, Stage,
)
from .opencode_adapter import OpenCodeAdapter
from .mock_data import SAMPLE_TICKETS, SAMPLE_PRACTICES


class RCAError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AgentA1:
    def __init__(self, opencode: Optional[OpenCodeAdapter] = None) -> None:
        self.opencode = opencode or OpenCodeAdapter(
            binary=config.opencode_binary,
            model=config.llm.query_model,
        )

    def run(self, bug_info: BugInfo) -> A1Output:
        if bug_info.link:
            ticket = self._fetch_ticket(bug_info.link)
            if ticket is not None:
                bug_info = self._merge_ticket(bug_info, ticket)
            elif not bug_info.description:
                raise RCAError("A1_BUG_FETCH_ERROR", "Bug 单拉取失败且无描述")

        symptoms = self._extract_symptoms(bug_info)
        error_type = bug_info.error_type or self._infer_error_type(symptoms)
        query = self._build_query(bug_info, symptoms, error_type)
        suspect_services = self._locate_suspect_services(bug_info, symptoms)

        return A1Output(
            symptoms=symptoms,
            error_type=error_type,
            query=query,
            suspect_services=suspect_services,
        )

    def _fetch_ticket(self, link: str) -> Optional[dict[str, Any]]:
        for t in SAMPLE_TICKETS:
            if link and t["ticket_id"] in link:
                return t
        return None

    def _merge_ticket(self, bug_info: BugInfo, ticket: dict[str, Any]) -> BugInfo:
        return bug_info.model_copy(
            update={
                "bug_id": bug_info.bug_id or ticket["ticket_id"],
                "title": bug_info.title or ticket["title"],
                "description": bug_info.description or ticket["description"],
                "error_type": bug_info.error_type or ticket["error_code"],
            }
        )

    def _extract_symptoms(self, bug_info: BugInfo) -> list[str]:
        symptoms: list[str] = []
        if bug_info.title:
            symptoms.append(bug_info.title)
        if bug_info.description:
            head = bug_info.description[:200]
            symptoms.append(head)
        if bug_info.stack:
            symptoms.append("栈: " + " <- ".join(bug_info.stack[:5]))
        if not symptoms:
            symptoms = ["未知症状"]
        return symptoms

    def _infer_error_type(self, symptoms: list[str]) -> str:
        joined = " ".join(symptoms)
        for ticket in SAMPLE_TICKETS:
            if ticket["error_code"] and ticket["error_code"].lower() in joined.lower():
                return ticket["error_code"]
        for ticket in SAMPLE_TICKETS:
            if any(kw in joined for kw in ticket["title"].split()):
                return ticket["error_code"] or "UNKNOWN"
        return "UNKNOWN"

    def _build_query(self, bug_info: BugInfo, symptoms: list[str], error_type: str) -> str:
        parts = []
        if error_type and error_type != "UNKNOWN":
            parts.append(error_type)
        if bug_info.description:
            parts.append(bug_info.description[:150])
        if not parts:
            parts = symptoms[:2]
        return " ".join(parts)

    def _locate_suspect_services(self, bug_info: BugInfo, symptoms: list[str]) -> list[str]:
        joined = " ".join(symptoms)
        matched = [t["microservice"] for t in SAMPLE_TICKETS if t["microservice"] in joined]
        if matched:
            return matched
        if bug_info.environment.get("service"):
            return [bug_info.environment["service"]]
        if config.runtime_mode == "mock_demo" and SAMPLE_TICKETS:
            return [SAMPLE_TICKETS[0]["microservice"]]
        return []


class AgentA2:
    def __init__(self) -> None:
        self.codegraph = None

    def run(self, suspect_services: list[str], error_stack: list[str]) -> list[SuspectFunction]:
        funcs: list[SuspectFunction] = []

        for ticket in SAMPLE_TICKETS:
            if ticket["microservice"] in suspect_services or not suspect_services:
                module = ticket["module"]
                func = SuspectFunction(
                    function_id=f"{ticket['microservice']}::{module}",
                    function_name=module,
                    call_path=[t["title"].split("：")[0][:40] for t in SAMPLE_TICKETS if t["module"] == module] or [],
                    static_depth=1.0,
                )
                funcs.append(func)

        if not funcs and config.runtime_mode == "mock_demo":
            funcs.append(SuspectFunction(
                function_id="order-center::OrderLockService.acquire",
                function_name="OrderLockService.acquire",
                call_path=["OrderService.create", "OrderLockService.acquire"],
                static_depth=2.0,
            ))

        return funcs


class AgentA3:
    def run(self, suspect_services: list[str]) -> AnomalyPath:
        if config.runtime_mode == "mock_demo":
            return AnomalyPath(
                span_tree={},
                propagation_path=suspect_services,
                functions=[f"{s}::handler" for s in suspect_services],
                runtime_anomaly=0.8,
            )
        return AnomalyPath(
            span_tree={},
            propagation_path=[],
            functions=[],
            runtime_anomaly=0.0,
        )


class AgentA4:
    def run(self, S_static: list[SuspectFunction], P_runtime: AnomalyPath) -> list[RootCause]:
        top3: list[RootCause] = []
        for f in S_static:
            confidence = self._confidence(f, P_runtime)
            cause = self._cause_text(f)
            top3.append(RootCause(
                root_cause=cause,
                confidence=confidence,
                evidence_chain=[
                    f"静态可达: {f.function_name} (depth={f.static_depth})",
                    f"运行时异常: {f.function_name in P_runtime.functions}",
                ],
                located_function=f.function_name,
                file=f.file or "",
                line=f.line or 0,
            ))

        top3.sort(key=lambda r: r.confidence, reverse=True)
        return top3[:3]

    def _confidence(self, f: SuspectFunction, P_runtime: AnomalyPath) -> float:
        base = min(f.static_depth / 5.0, 1.0) * 0.5
        if f.function_name in [p.rsplit("::", 1)[-1] for p in P_runtime.functions]:
            base += P_runtime.runtime_anomaly * 0.5
        return round(min(base + 0.2, 0.98), 2)

    def _cause_text(self, f: SuspectFunction) -> str:
        for t in SAMPLE_TICKETS:
            if t["module"] == f.function_name:
                return t["root_cause"]
        return f"{f.function_name} 存在潜在根因，需人工确认"


class AgentA5:
    def __init__(self, opencode: Optional[OpenCodeAdapter] = None) -> None:
        self.opencode = opencode or OpenCodeAdapter(
            binary=config.opencode_binary,
            model=config.llm.query_model,
        )

    def run(self, top3: list[RootCause], error_type: str = "") -> Solution:
        historical: list[str] = []
        best_practices: list[str] = []

        for rc in top3:
            for t in SAMPLE_TICKETS:
                if t["module"] == rc.located_function or t["error_code"] == error_type:
                    if t["fix_code"] and t["fix_code"] not in historical:
                        historical.append(t["fix_code"])
                    if t["root_cause"] and t["root_cause"] not in best_practices:
                        best_practices.append(t["root_cause"])

        for p in SAMPLE_PRACTICES:
            entry = f"{p['title']}：{p['content']}（{p['source']}）"
            if entry not in best_practices:
                best_practices.append(entry)

        patch = self._compose_patch(top3, historical)
        test_cases = [f"新增验证用例覆盖 {rc.located_function} 的根因场景" for rc in top3]

        return Solution(
            patch_suggestion=patch,
            test_cases=test_cases,
            historical_cases=historical,
            best_practices=best_practices,
        )

    def _compose_patch(self, top3: list[RootCause], historical: list[str]) -> str:
        if not top3:
            return "未生成修复方案"
        lines = [f"针对根因 {top3[0].located_function}："]
        if historical:
            lines.append("参考历史修复:")
            for h in historical[:2]:
                lines.append(f"- {h}")
        return "\n".join(lines)