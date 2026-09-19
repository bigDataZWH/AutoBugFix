from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from .config import config
from .models import (
    A1Output, AnomalyPath, BugInfo, RootCause, Solution,
    SuspectFunction, Stage,
)
from .opencode_adapter import OpenCodeAdapter
from .mock_data import SAMPLE_TICKETS, SAMPLE_PRACTICES

if TYPE_CHECKING:
    from .retriever import Retriever


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

        llm_out = self._llm_analyze(bug_info)
        if llm_out is not None:
            return llm_out

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

    def _llm_analyze(self, bug_info: BugInfo) -> Optional[A1Output]:
        """opencode headless 驱动：让 LLM 提取症状/错误类型/查询/嫌疑服务。

        解析失败或二进制不可用时返回 None，由调用方降级到硬编码逻辑。
        """
        severity = getattr(bug_info, "severity", "") or bug_info.environment.get("severity", "")
        component = getattr(bug_info, "component", "") or bug_info.environment.get("service", "")
        prompt = (
            "你是根因分析流水线的故障接入 Agent(A1)。根据给定 Bug 信息，提取故障症状、"
            "推断错误类型、构建检索查询、定位嫌疑微服务。仅输出严格 JSON，字段："
            "symptoms[list[str]], error_type[str], query[str], suspect_services[list[str]]。"
            "不要输出 JSON 以外的文字。\n\n"
            f"【Bug 信息】\n"
            f"bug_id: {bug_info.bug_id}\n"
            f"title: {bug_info.title}\n"
            f"description: {bug_info.description}\n"
            f"stack: {bug_info.stack}\n"
            f"severity: {severity}\n"
            f"component: {component}\n"
        )
        parsed = self.opencode.query_structured(prompt)
        if not parsed:
            return None
        symptoms = parsed.get("symptoms") or []
        error_type = parsed.get("error_type") or ""
        query = parsed.get("query") or ""
        suspect_services = parsed.get("suspect_services") or []
        if not isinstance(symptoms, list) or not isinstance(suspect_services, list):
            return None
        if not symptoms and not query:
            return None
        symptoms = [str(s) for s in symptoms if s]
        suspect_services = [str(s) for s in suspect_services if s]
        if not error_type:
            error_type = bug_info.error_type or "UNKNOWN"
        return A1Output(
            symptoms=symptoms,
            error_type=error_type,
            query=query,
            suspect_services=suspect_services,
        )

    def _fetch_ticket(self, link: str) -> Optional[dict[str, Any]]:
        if link and self.opencode.available:
            raw = self.opencode.fetch_yunjie_tickets([link])
            if raw and raw[0].get("ticket_id"):
                return raw[0]
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
    def __init__(self, opencode: Optional[OpenCodeAdapter] = None) -> None:
        self.opencode = opencode or OpenCodeAdapter(
            binary=config.opencode_binary,
            model=config.llm.extract_model,
        )

    def run(self, suspect_services: list[str], error_stack: list[str]) -> list[SuspectFunction]:
        llm_funcs = self._llm_analyze(suspect_services, error_stack)
        if llm_funcs:
            return llm_funcs
        return self._mock_analyze(suspect_services)

    def _llm_analyze(self, suspect_services: list[str], error_stack: list[str]) -> list[SuspectFunction]:
        """opencode headless 驱动：让 LLM 分析静态调用图，输出嫌疑函数。

        解析失败或二进制不可用时返回空列表，由调用方降级到 mock。
        """
        prompt = (
            "你是根因分析流水线的静态分析 Agent(A2)。根据嫌疑微服务与错误栈，"
            "分析静态调用图，定位最可疑的函数。仅输出严格 JSON，字段："
            "suspects[{function_id[str], function_name[str], "
            "call_path[list[str]], static_depth[float]}]。"
            "static_depth 表示该函数在静态调用链中的深度权重(0-5)。"
            "不要输出 JSON 以外的文字。\n\n"
            f"【嫌疑微服务】\n{suspect_services}\n\n"
            f"【错误栈】\n{error_stack}\n"
        )
        parsed = self.opencode.query_structured(prompt)
        suspects = parsed.get("suspects") if parsed else None
        if not isinstance(suspects, list) or not suspects:
            return []
        funcs: list[SuspectFunction] = []
        for s in suspects:
            if not isinstance(s, dict):
                continue
            fid = str(s.get("function_id") or "").strip()
            fname = str(s.get("function_name") or "").strip()
            if not fid:
                continue
            cp = s.get("call_path") or []
            if not isinstance(cp, list):
                cp = []
            try:
                depth = float(s.get("static_depth") or 1.0)
            except (TypeError, ValueError):
                depth = 1.0
            funcs.append(SuspectFunction(
                function_id=fid,
                function_name=fname or fid,
                call_path=[str(c) for c in cp],
                static_depth=depth,
            ))
        return funcs

    def _mock_analyze(self, suspect_services: list[str]) -> list[SuspectFunction]:
        """兜底：基于 SAMPLE_TICKETS 的静态嫌疑函数生成。"""
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
    def __init__(
        self,
        trace_data: Optional[Any] = None,
        cmdb: Optional[Any] = None,
        opencode: Optional[OpenCodeAdapter] = None,
    ) -> None:
        self._trace_data = trace_data
        self._cmdb = cmdb
        self.opencode = opencode or OpenCodeAdapter(
            binary=config.opencode_binary,
            model=config.llm.extract_model,
        )

    def run(self, suspect_services: list[str]) -> AnomalyPath:
        llm_path = self._llm_analyze(suspect_services)
        if llm_path is not None:
            return llm_path
        return self._mock_analyze(suspect_services)

    def _llm_analyze(self, suspect_services: list[str]) -> Optional[AnomalyPath]:
        """opencode headless 驱动：让 LLM 分析运行时异常传播路径。

        解析失败或二进制不可用时返回 None，由调用方降级到 mock。
        """
        prompt = (
            "你是根因分析流水线的运行时分析 Agent(A3)。根据嫌疑微服务，"
            "分析运行时异常的传播路径与异常函数集合。仅输出严格 JSON，字段："
            "propagation_path[list[str]], functions[list[str]], "
            "runtime_anomaly[float, 0-1]。runtime_anomaly 表示运行时异常置信度。"
            "不要输出 JSON 以外的文字。\n\n"
            f"【嫌疑微服务】\n{suspect_services}\n"
        )
        parsed = self.opencode.query_structured(prompt)
        if not parsed:
            return None
        propagation = parsed.get("propagation_path") or []
        functions = parsed.get("functions") or []
        if not isinstance(propagation, list) or not isinstance(functions, list):
            return None
        if not functions:
            return None
        try:
            anomaly = float(parsed.get("runtime_anomaly") or 0.0)
        except (TypeError, ValueError):
            anomaly = 0.0
        propagation = [str(s) for s in propagation if s]
        functions = [str(f) for f in functions if f]
        if not functions:
            return None
        return AnomalyPath(
            span_tree={},
            propagation_path=propagation,
            functions=functions,
            runtime_anomaly=anomaly,
        )

    def _mock_analyze(self, suspect_services: list[str]) -> AnomalyPath:
        """兜底：基于 SAMPLE_TICKETS 的运行时异常路径生成。"""
        if config.runtime_mode == "mock_demo":
            functions = [
                f"{t['microservice']}::{t['module']}"
                for t in SAMPLE_TICKETS
                if t["microservice"] in suspect_services or not suspect_services
            ]
            if not functions:
                functions = [f"{s}::handler" for s in suspect_services]
            return AnomalyPath(
                span_tree={},
                propagation_path=suspect_services,
                functions=functions,
                runtime_anomaly=0.8,
            )
        return AnomalyPath(
            span_tree={},
            propagation_path=[],
            functions=[],
            runtime_anomaly=0.0,
        )


class AgentA5:
    def __init__(self, opencode: Optional[OpenCodeAdapter] = None, retriever: Optional["Retriever"] = None) -> None:
        self.opencode = opencode or OpenCodeAdapter(
            binary=config.opencode_binary,
            model=config.llm.query_model,
        )
        self.retriever = retriever

    def run(self, top3: list[RootCause], error_type: str = "") -> Solution:
        historical: list[str] = []
        best_practices: list[str] = []

        if self.retriever is not None:
            for rc in top3:
                q = f"{rc.root_cause} {rc.located_function} {error_type}"
                matches = self.retriever.hybrid_search(q, top_k=5)
                for m in matches:
                    if m.fix_code and m.fix_code not in historical:
                        historical.append(m.fix_code)
                    if m.root_cause and m.root_cause not in best_practices:
                        best_practices.append(m.root_cause)

        llm_sol = self._llm_synthesize(top3, error_type, historical, best_practices)
        if llm_sol is not None:
            return llm_sol

        if not historical:
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

    def _llm_synthesize(
        self,
        top3: list[RootCause],
        error_type: str,
        historical: list[str],
        best_practices: list[str],
    ) -> Optional[Solution]:
        """opencode headless 驱动：让 LLM 合成修复方案。

        解析失败或二进制不可用时返回 None，由调用方降级到 retriever/mock。
        """
        root_cause_brief = "; ".join(
            f"{rc.located_function or rc.root_cause}(置信度={rc.confidence})"
            for rc in top3 if rc.located_function
        )
        prompt = (
            "你是根因分析流水线的方案生成 Agent(A5)。根据 Top-3 根因与已知历史案例，"
            "生成修复补丁建议、回归测试用例、历史案例引用与最佳实践。仅输出严格 JSON，字段："
            "patch_suggestion[str], test_cases[list[str]], "
            "historical_cases[list[str]], best_practices[list[str]]。"
            "不要输出 JSON 以外的文字。\n\n"
            f"【Top-3 根因】\n{root_cause_brief}\n\n"
            f"【错误类型】\n{error_type}\n\n"
            f"【已检索历史案例】\n{historical[:5]}\n\n"
            f"【已知最佳实践】\n{best_practices[:5]}\n"
        )
        parsed = self.opencode.query_structured(prompt)
        if not parsed:
            return None
        patch = parsed.get("patch_suggestion")
        if not isinstance(patch, str) or not patch.strip():
            return None
        tc = parsed.get("test_cases") or []
        hc = parsed.get("historical_cases") or []
        bp = parsed.get("best_practices") or []
        if not isinstance(tc, list) or not isinstance(hc, list) or not isinstance(bp, list):
            return None
        merged_bp = list(best_practices)
        for p in bp:
            if isinstance(p, str) and p not in merged_bp:
                merged_bp.append(p)
        for p in SAMPLE_PRACTICES:
            entry = f"{p['title']}：{p['content']}（{p['source']}）"
            if entry not in merged_bp:
                merged_bp.append(entry)
        return Solution(
            patch_suggestion=patch,
            test_cases=[str(t) for t in tc if t] or [f"新增验证用例覆盖 {rc.located_function} 的根因场景" for rc in top3],
            historical_cases=[str(h) for h in hc if h] or historical,
            best_practices=merged_bp,
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