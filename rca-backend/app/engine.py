"""opencode serve headless RCA 引擎编排器。

V3 RCAEngine 将原 5-Agent 编排（A1→A2‖A3→A4→CRAG→HIL→A5）替换为单次 opencode serve
会话：opencode 在用户指定本地代码仓上完成 理解→codegraph 分析→Top-3 定位→CRAG 自评迭代→方案生成，
Python 层仅保留 HIL 人工闸门、SSE 事件代理、RCAState 状态持久化与断点续跑。

拓扑: START->OPENCODE_SESSION(单会话)->HIL->END。
serve 不可用或 mock_demo 模式时降级到 mock 路径（SAMPLE_TICKETS 派生 top3/solution）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from .agents import RCAError
from .config import config
from .flywheel import flywheel
from .gates import crag_gate, hil_gate, process_hil_decision
from .mock_data import SAMPLE_TICKETS
from .models import (
    AnomalyPath, Candidate, Evidence, GateStatus, HilDecision, HilResult,
    RCAState, RootCause, Solution, SolutionDiff, SolutionStep, Stage,
)
from .opencode_serve_adapter import OpenCodeServeAdapter, OpenCodeServeError
from .rca_prompt import build_patch_prompt, build_rca_prompt

logger = logging.getLogger(__name__)


class SSEEventBus:
    """SSE 事件总线：阶段事件写入 Redis list，前端订阅补发。

    Redis 不可用时回退到进程内内存存储（多 worker 场景请配置真实 Redis）。
    """

    PREFIX = "rca:events:"
    TTL = 86400

    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self._redis = redis_client
        self._memory: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client

    def publish(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": data, "ts": time.time()}
        if self._redis is not None:
            try:
                self._redis.rpush(f"{self.PREFIX}{task_id}", json.dumps(payload, ensure_ascii=False, default=str))
                self._redis.expire(f"{self.PREFIX}{task_id}", self.TTL)
                return
            except Exception as e:
                logger.warning("SSE publish Redis 失败，回退内存: task_id=%s event=%s err=%s", task_id, event, e)
        with self._lock:
            self._memory.setdefault(task_id, []).append(payload)

    def replay(self, task_id: str, last_event_id: int = 0) -> list[dict[str, Any]]:
        if self._redis is not None:
            try:
                raw = self._redis.lrange(f"{self.PREFIX}{task_id}", last_event_id, -1)
                return [json.loads(r) for r in raw]
            except Exception as e:
                logger.warning("SSE replay Redis 失败，回退内存: task_id=%s err=%s", task_id, e)
        with self._lock:
            return self._memory.get(task_id, [])[last_event_id:]


class StateStore:
    """RCAState 持久化，支持断点续跑。key = rca:state:{task_id}，TTL 24h。"""

    PREFIX = "rca:state:"
    TTL = 86400

    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self._redis = redis_client
        self._memory: dict[str, str] = {}

    def _set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client

    def save(self, state: RCAState) -> None:
        serialized = state.model_dump_json()
        if self._redis is not None:
            try:
                self._redis.set(f"{self.PREFIX}{state.task_id}", serialized, ex=self.TTL)
                return
            except Exception as e:
                logger.warning("StateStore save Redis 失败，回退内存: task_id=%s err=%s", state.task_id, e)
        self._memory[state.task_id] = serialized

    def load(self, task_id: str) -> Optional[RCAState]:
        if self._redis is not None:
            try:
                raw = self._redis.get(f"{self.PREFIX}{task_id}")
            except Exception as e:
                logger.warning("StateStore load Redis 失败，回退内存: task_id=%s err=%s", task_id, e)
                raw = None
        else:
            raw = self._memory.get(task_id)
        if not raw:
            return None
        try:
            return RCAState.model_validate_json(raw)
        except Exception as e:
            logger.warning("StateStore 反序列化失败: task_id=%s err=%s", task_id, e)
            return None


class RCAEngine:
    """opencode serve headless RCA 编排入口。"""

    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self.serve: Optional[OpenCodeServeAdapter] = None
        self.events = SSEEventBus(redis_client)
        self.store = StateStore(redis_client)

    def set_redis(self, redis_client: Any) -> None:
        self.events._set_redis(redis_client)
        self.store._set_redis(redis_client)

    def set_serve_adapter(self, adapter: Optional[OpenCodeServeAdapter]) -> None:
        self.serve = adapter

    @staticmethod
    def generate_task_id() -> str:
        today = datetime.now().strftime("%Y%m%d")
        seq = str(uuid.uuid4().int)[:4]
        return f"rca-{today}-{seq}"

    # ------------------------------------------------------------------
    # 顺序编排
    # ------------------------------------------------------------------
    def run_sequential(self, state: RCAState) -> RCAState:
        try:
            self._ensure_task_id(state)
            self.store.save(state)

            # Stage 1: opencode serve 单会话（分析+CRAG自评+方案）
            if state.stage.index < 4:
                state = self._apply_opencode_session(state)

            # Stage 2: HIL 后置闸门（Python）
            if state.gate_status.hil == "pending":
                self.store.save(state)
                return state

            if state.gate_status.hil not in ("confirmed", "modified", "rejected"):
                state = self._apply_hil(state)
                if state.gate_status.hil == "pending":
                    self.store.save(state)
                    return state

            state.stage = Stage(index=6, name="COMPLETED", status="completed")
            state.gate_status = GateStatus(
                crag="passed" if state.gate_status.crag == "relevant" else state.gate_status.crag,
                hil="skipped" if state.gate_status.hil in ("pending", "skipped") else state.gate_status.hil,
            )
            self.store.save(state)
            self.events.publish(state.task_id, "final", {
                "top3": [r.model_dump() for r in state.top3],
                "solution": state.solution.model_dump() if state.solution else {},
                "gate_status": state.gate_status.model_dump(),
            })
            payload = flywheel.extract_payload(
                root_cause=state.top3[0].root_cause if state.top3 else "",
                root_cause_function=state.top3[0].located_function if state.top3 else "",
                call_path=state.P_runtime.functions,
                fix_patch=state.solution.patch_suggestion if state.solution else "",
                verify_case="; ".join(state.solution.test_cases) if state.solution else "",
                ticket_id=state.bug_info.bug_id,
                title=state.bug_info.title,
                description=state.bug_info.description,
            )
            wb_result = flywheel.writeback_sync(payload)
            if wb_result.inserted == 0:
                self.events.publish(state.task_id, "flywheel_skipped", {
                    "reason": "dedup_or_error",
                    "ticket_id": state.bug_info.bug_id,
                })
            return state
        except RCAError as exc:
            return self._fail(state, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            return self._fail(state, "UNKNOWN", str(exc))

    def run(self, state: RCAState) -> RCAState:
        return self.run_sequential(state)

    # ------------------------------------------------------------------
    # 阶段执行
    # ------------------------------------------------------------------
    def _ensure_task_id(self, state: RCAState) -> None:
        if not state.task_id:
            state.task_id = self.generate_task_id()

    def _apply_opencode_session(self, state: RCAState) -> RCAState:
        self._publish_stage(state, "OPENCODE_SESSION", "start", {})
        repo_path = self._resolve_repo_path(state)

        use_serve = (
            state.runtime_mode != "mock_demo"
            and self.serve is not None
            and self.serve.available
            and bool(repo_path)
        )

        if use_serve:
            sid: Optional[str] = None
            try:
                prompt = build_rca_prompt(state.bug_info, repo_path, config.gate.max_rewrite_rounds)
                sid = self.serve.create_session(repo_path)  # type: ignore[union-attr]
                raw = self.serve.prompt_async(  # type: ignore[union-attr]
                    sid, prompt,
                    on_event=lambda e: self._proxy_event(state, e),
                )
                self._map_opencode_output(raw, state)
                state = self._crag_retry_if_needed(state, repo_path)
            except OpenCodeServeError as e:
                logger.warning("opencode serve 会话失败，降级 mock: task_id=%s code=%s", state.task_id, e.code)
                self._fallback_mock(state)
            except Exception as e:  # noqa: BLE001
                logger.warning("opencode serve 会话异常，降级 mock: task_id=%s err=%s", state.task_id, e)
                self._fallback_mock(state)
            finally:
                if sid and self.serve is not None:
                    self.serve.close_session(sid)
        else:
            self._fallback_mock(state)

        state.stage = Stage(index=4, name="OPENCODE_SESSION", status="completed")
        self.store.save(state)
        self._publish_stage(state, "OPENCODE_SESSION", "complete", {
            "degraded": state.degraded,
            "top3_count": len(state.top3),
        })
        return state

    def _apply_hil(self, state: RCAState) -> RCAState:
        if state.gate_status.hil in ("confirmed", "modified", "rejected"):
            return state
        candidates = [self._rootcause_to_candidate(rc) for rc in state.top3]
        valid = [c for c in candidates if c is not None]

        top_confidence = state.top3[0].confidence if state.top3 else 0.0
        hil_result = hil_gate(valid, top_confidence, task_id=state.task_id)
        if hil_result.action == "hang" and hil_result.panel_payload:
            if state.runtime_mode == "mock_demo":
                state.gate_status.hil = "skipped"
            else:
                state.gate_status.hil = "pending"
                self.events.publish(state.task_id, "gate_pending", {
                    "gate": "HIL",
                    "reason": "low_confidence",
                    "top_confidence": top_confidence,
                    "payload": hil_result.panel_payload.model_dump(),
                })
        else:
            state.gate_status.hil = "skipped"
        self.store.save(state)
        return state

    def _apply_patch_session(self, state: RCAState, modified_top3: list[dict[str, Any]]) -> RCAState:
        if not self.serve or not self.serve.available:
            self.events.publish(state.task_id, "patch_skipped", {
                "reason": "serve_unavailable",
                "message": "opencode serve 不可用，保留原方案",
            })
            return state
        repo = self._resolve_repo_path(state)
        if not repo:
            self.events.publish(state.task_id, "patch_skipped", {
                "reason": "no_repo_path",
                "message": "未提供 repo_path，跳过 patch 会话",
            })
            return state
        sid: Optional[str] = None
        try:
            prompt = build_patch_prompt(modified_top3, state.bug_info)
            sid = self.serve.create_session(repo)
            raw = self.serve.prompt_async(
                sid, prompt,
                on_event=lambda e: self._proxy_event(state, e),
            )
            sol = raw.get("solution") if isinstance(raw, dict) else None
            if isinstance(sol, dict):
                state.solution = self._map_solution(sol)
        except Exception as e:  # noqa: BLE001
            logger.warning("patch 会话失败，保留原 solution: task_id=%s err=%s", state.task_id, e)
            self.events.publish(state.task_id, "patch_skipped", {
                "reason": "patch_session_failed",
                "message": str(e),
            })
        finally:
            if sid:
                self.serve.close_session(sid)
        return state

    def _crag_retry_if_needed(self, state: RCAState, repo_path: str) -> RCAState:
        """CRAG 自评 Python 兜底：若 verdict 为 irrelevant/ambiguous，追加补充 prompt 重发会话。"""
        max_rounds = config.gate.max_rewrite_rounds
        for attempt in range(1, max_rounds + 1):
            if state.gate_status.crag == "relevant":
                break
            if not self.serve or not self.serve.available:
                break
            logger.info("CRAG 补强第 %d 轮: task_id=%s verdict=%s", attempt, state.task_id, state.gate_status.crag)
            self.events.publish(state.task_id, "crag_retry", {
                "attempt": attempt,
                "verdict": state.gate_status.crag,
                "max_rounds": max_rounds,
            })
            sid: str | None = None
            try:
                retry_prompt = (
                    f"上一轮 CRAG verdict={state.gate_status.crag}，证据不充分。\n"
                    f"请补充 codegraph 分析，重新评估四维证据并更新 crag_verdict 与 top3/solution。\n"
                    f"只输出严格 JSON，schema 同前。"
                )
                sid = self.serve.create_session(repo_path)
                raw = self.serve.prompt_async(sid, retry_prompt)
                self._map_opencode_output(raw, state)
            except Exception as e:  # noqa: BLE001
                logger.warning("CRAG 补强第 %d 轮失败: task_id=%s err=%s", attempt, state.task_id, e)
                break
            finally:
                if sid and self.serve is not None:
                    self.serve.close_session(sid)
        return state

    # ------------------------------------------------------------------
    # HIL 回灌 / 断点续跑
    # ------------------------------------------------------------------
    def resume(self, task_id: str, decision: HilDecision) -> RCAState:
        state = self.store.load(task_id)
        if state is None:
            raise RCAError("STATE_NOT_FOUND", f"任务 {task_id} 不存在")

        candidates = [self._rootcause_to_candidate(rc) for rc in state.top3]
        valid = [c for c in candidates if c is not None]
        updated, action = process_hil_decision(decision, valid)

        if action == "rejected":
            state.gate_status.hil = "rejected"
            state.stage = Stage(index=99, name="REJECTED", status="rejected")
            self.store.save(state)
            self.events.publish(state.task_id, "gate_resolved", {"gate": "HIL", "action": "rejected"})
            return state

        if updated and isinstance(updated[0], dict):
            state.top3 = self._map_top3_dicts(updated)
        else:
            state.top3 = self._candidates_to_rootcauses(updated)
        state.gate_status.hil = "confirmed" if action == "confirmed" else "modified"
        state.stage = Stage(index=4, name="OPENCODE_SESSION", status="completed")
        self.store.save(state)
        self.events.publish(state.task_id, "gate_resolved", {"gate": "HIL", "action": action})

        if action == "modified" and decision.modified_top3:
            state = self._apply_patch_session(state, decision.modified_top3)

        result = self.run_sequential(state)
        return result

    def resume_from_checkpoint(self, task_id: str) -> RCAState:
        state = self.store.load(task_id)
        if state is None:
            raise RCAError("STATE_NOT_FOUND", f"任务 {task_id} 不存在")
        if state.stage.status == "failed" or state.stage.index >= 99:
            return state
        return self.run_sequential(state)

    def get_state(self, task_id: str) -> Optional[RCAState]:
        return self.store.load(task_id)

    def drain_events(self, task_id: str, last_event_id: int = 0) -> tuple[list[dict[str, Any]], int]:
        """拉取 SSEEventBus 中该 task 的增量事件，返回 (events, next_offset)。

        供 main.py 的 _run() 协程在 engine.run_sequential 执行期间轮询中继到 asyncio.Queue，
        实现 opencode 实时进度/阶段切换/HIL pending 等事件透传到前端 SSE 流。
        """
        events = self.events.replay(task_id, last_event_id)
        return events, last_event_id + len(events)

    # ------------------------------------------------------------------
    # opencode 输出映射 / 事件代理
    # ------------------------------------------------------------------
    def _proxy_event(self, state: RCAState, evt: dict[str, Any]) -> None:
        try:
            etype = evt.get("event") or evt.get("type") or "opencode"
            self.events.publish(state.task_id, "opencode_event", {
                "stage": "OPENCODE_SESSION",
                "event": etype,
                "data": evt,
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("proxy_event 失败（已忽略）: %s", e)

    def _map_opencode_output(self, raw: Any, state: RCAState) -> None:
        if not isinstance(raw, dict) or not raw:
            self._fallback_mock(state)
            return

        state.symptoms = raw.get("symptoms") or state.symptoms or []
        if raw.get("error_type"):
            state.error_type = str(raw["error_type"])
        if raw.get("query"):
            state.query = str(raw["query"])
        state.suspect_services = raw.get("suspect_services") or state.suspect_services or []

        verdict = raw.get("crag_verdict")
        if verdict in ("relevant", "ambiguous", "irrelevant"):
            state.gate_status.crag = verdict

        raw_top3 = raw.get("top3")
        if isinstance(raw_top3, list) and raw_top3:
            state.top3 = self._map_top3_dicts(raw_top3)

        sol = raw.get("solution")
        if isinstance(sol, dict):
            state.solution = self._map_solution(sol)

        funcs = [r.located_function for r in state.top3 if r.located_function]
        state.P_runtime = AnomalyPath(functions=funcs)
        state.degraded = False

    def _map_top3_dicts(self, dicts: list[Any]) -> list[RootCause]:
        top3: list[RootCause] = []
        for d in dicts:
            if not isinstance(d, dict):
                continue
            ev_raw = d.get("evidence") or {}
            ev = Evidence(
                static_depth=self._as_float(ev_raw.get("static_depth")),
                runtime_anomaly=self._as_float(ev_raw.get("runtime_anomaly")),
                metric_corr=self._as_float(ev_raw.get("metric_corr")),
                change_recency=self._as_float(ev_raw.get("change_recency")),
            )
            top3.append(RootCause(
                root_cause=str(d.get("root_cause", "")),
                confidence=round(min(max(self._as_float(d.get("confidence")), 0.0), 1.0), 2),
                evidence_chain=[str(x) for x in (d.get("evidence_chain") or [])],
                located_function=str(d.get("located_function", "")),
                file=str(d.get("file", "")),
                line=self._as_int(d.get("line")),
                evidence=ev,
            ))
        while len(top3) < 3:
            top3.append(RootCause(root_cause="insufficient_evidence", confidence=0.0))
        return top3[:3]

    def _map_solution(self, sol: dict[str, Any]) -> Solution:
        diffs: list[SolutionDiff] = []
        for d in (sol.get("diffs") or []):
            if isinstance(d, dict):
                diffs.append(SolutionDiff(
                    file=str(d.get("file", "")),
                    before=str(d.get("before", "")),
                    after=str(d.get("after", "")),
                    summary=str(d.get("summary", "")),
                ))
        steps: list[SolutionStep] = []
        for s in (sol.get("steps") or []):
            if isinstance(s, dict):
                steps.append(SolutionStep(
                    step=self._as_int(s.get("step")) or (len(steps) + 1),
                    action=str(s.get("action", "")),
                    detail=str(s.get("detail", "")),
                ))
        return Solution(
            diffs=diffs,
            steps=steps,
            verify_expected=dict(sol.get("verify_expected") or {}),
            patch_suggestion=str(sol.get("patch_suggestion", "")),
            test_cases=[str(t) for t in (sol.get("test_cases") or [])],
            historical_cases=[str(c) for c in (sol.get("historical_cases") or [])],
            best_practices=[str(p) for p in (sol.get("best_practices") or [])],
        )

    # ------------------------------------------------------------------
    # 降级 mock
    # ------------------------------------------------------------------
    def _fallback_mock(self, state: RCAState) -> None:
        state.degraded = True
        tickets = SAMPLE_TICKETS[:3]
        top3: list[RootCause] = []
        for i, t in enumerate(tickets):
            ev = Evidence(
                static_depth=round(0.70 - i * 0.10, 2),
                runtime_anomaly=round(0.65 - i * 0.10, 2),
                metric_corr=round(0.60 - i * 0.10, 2),
                change_recency=round(0.55 - i * 0.10, 2),
            )
            top3.append(RootCause(
                root_cause=t.get("root_cause", ""),
                confidence=round(min(0.65 - i * 0.10, 0.98), 2),
                evidence_chain=[
                    f"静态可达 static_depth={ev.static_depth:.2f}",
                    f"运行时异常 runtime_anomaly={ev.runtime_anomaly:.2f}",
                    f"指标关联 metric_corr={ev.metric_corr:.2f}",
                    f"变更时效 change_recency={ev.change_recency:.2f}",
                ],
                located_function=t.get("module", ""),
                file="",
                line=0,
                evidence=ev,
            ))
        while len(top3) < 3:
            top3.append(RootCause(root_cause="insufficient_evidence", confidence=0.0))
        state.top3 = top3
        state.symptoms = [t.get("title", "") for t in tickets] or ["insufficient_symptoms"]
        state.error_type = SAMPLE_TICKETS[0].get("error_code", "") if SAMPLE_TICKETS else ""
        state.suspect_services = [t.get("microservice", "") for t in tickets]
        mock_evidence = [r.evidence for r in top3 if r.evidence is not None]
        crag_result = crag_gate(mock_evidence)
        state.gate_status.crag = crag_result.verdict
        state.P_runtime = AnomalyPath(functions=[r.located_function for r in top3 if r.located_function])
        state.solution = self._mock_solution()

    def _mock_solution(self) -> Solution:
        t = SAMPLE_TICKETS[0] if SAMPLE_TICKETS else {}
        return Solution(
            diffs=[SolutionDiff(
                file="",
                before="",
                after=t.get("fix_code", ""),
                summary=t.get("title", ""),
            )],
            steps=[SolutionStep(step=1, action="修复", detail=t.get("fix_code", ""))],
            patch_suggestion=t.get("fix_code", ""),
            test_cases=["验证问题不再复现"],
            historical_cases=[t.get("title", "历史相似问题")] if t else [],
            best_practices=[
                "对高并发写路径增加分段锁与原子校验",
                "扣减后增加非负校验并记录告警",
            ],
        )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _resolve_repo_path(self, state: RCAState) -> str:
        env = state.bug_info.environment or {}
        return str(env.get("repo_path", "") or "")

    @staticmethod
    def _as_float(v: Any) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _as_int(v: Any) -> int:
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    def _publish_stage(self, state: RCAState, stage: str, status: str, summary: dict[str, Any]) -> None:
        if status == "start":
            self.events.publish(state.task_id, "stage_start", {"stage": stage, "ts": time.time()})
        else:
            self.events.publish(state.task_id, "stage_complete", {"stage": stage, "summary": summary})

    def _fail(self, state: RCAState, code: str, message: str) -> RCAState:
        state.stage = Stage(index=99, name="FAILED", status="failed", artifact={"code": code, "message": message})
        self.store.save(state)
        self.events.publish(state.task_id, "error", {"code": code, "message": message})
        return state

    def _candidates_to_rootcauses(self, candidates: list[Candidate]) -> list[RootCause]:
        top3: list[RootCause] = []
        for c in candidates:
            top3.append(RootCause(
                root_cause=c.function_name or c.function_id,
                confidence=round(min(c.score, 0.98), 2),
                evidence_chain=[
                    f"静态可达 static_depth={c.evidence.static_depth:.2f}",
                    f"运行时异常 runtime_anomaly={c.evidence.runtime_anomaly:.2f}",
                    f"指标关联 metric_corr={c.evidence.metric_corr:.2f}",
                    f"变更时效 change_recency={c.evidence.change_recency:.2f}",
                ],
                located_function=c.function_name or c.function_id,
                file=c.file,
                line=c.line,
                evidence=c.evidence,
            ))
        while len(top3) < 3:
            top3.append(RootCause(
                root_cause="insufficient_evidence",
                confidence=0.0,
                evidence_chain=[],
                located_function="",
            ))
        return top3[:3]

    def _rootcause_to_candidate(self, rc: RootCause) -> Optional[Candidate]:
        if not rc.located_function:
            return None
        if rc.evidence is not None:
            return Candidate(
                function_id=rc.located_function,
                function_name=rc.located_function,
                file=rc.file,
                line=rc.line,
                score=rc.confidence,
                evidence=rc.evidence,
            )
        evidence = Evidence()
        if rc.evidence_chain:
            for line in rc.evidence_chain:
                if "static_depth=" in line:
                    try:
                        evidence.static_depth = float(line.split("static_depth=")[1].split()[0])
                    except (IndexError, ValueError):
                        pass
                if "runtime_anomaly=" in line:
                    try:
                        evidence.runtime_anomaly = float(line.split("runtime_anomaly=")[1].split()[0])
                    except (IndexError, ValueError):
                        pass
                if "metric_corr=" in line:
                    try:
                        evidence.metric_corr = float(line.split("metric_corr=")[1].split()[0])
                    except (IndexError, ValueError):
                        pass
                if "change_recency=" in line:
                    try:
                        evidence.change_recency = float(line.split("change_recency=")[1].split()[0])
                    except (IndexError, ValueError):
                        pass
        return Candidate(
            function_id=rc.located_function,
            function_name=rc.located_function,
            file=rc.file,
            line=rc.line,
            score=rc.confidence,
            evidence=evidence,
        )


engine = RCAEngine()
