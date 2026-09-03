"""Spec 4: 5-Agent 智能引擎编排器。

拓扑: START->A1->(A2||A3 fan-out)->A4 fan-in->CRAG->HIL->A5->END。
LangGraph 可用时使用状态机；不可用时回退 SequentialOrchestrator。
SSE 事件推送 + Redis 状态持久化 + 断点续跑 + HIL 挂起/回灌。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from .agents import AgentA1, AgentA2, AgentA3, AgentA4, AgentA5, RCAError
from .config import config
from .dual_graph import cross_validate
from .flywheel import flywheel
from .gates import crag_gate, hil_gate, process_hil_decision
from .lightrag_adapter import lightrag
from .models import (
    A1Output, AnomalyPath, BugInfo, Candidate, Evidence, GateStatus,
    HilDecision, HilResult, RCAState, RootCause, Solution, Stage,
    SuspectFunction,
)


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
            except Exception:
                pass
        with self._lock:
            self._memory.setdefault(task_id, []).append(payload)

    def replay(self, task_id: str, last_event_id: int = 0) -> list[dict[str, Any]]:
        if self._redis is not None:
            try:
                raw = self._redis.lrange(f"{self.PREFIX}{task_id}", last_event_id, -1)
                return [json.loads(r) for r in raw]
            except Exception:
                pass
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
            except Exception:
                pass
        self._memory[state.task_id] = serialized

    def load(self, task_id: str) -> Optional[RCAState]:
        if self._redis is not None:
            try:
                raw = self._redis.get(f"{self.PREFIX}{task_id}")
            except Exception:
                raw = None
        else:
            raw = self._memory.get(task_id)
        if not raw:
            return None
        try:
            return RCAState.model_validate_json(raw)
        except Exception:
            return None


class RCAEngine:
    """5-Agent 引擎编排入口。"""

    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self.a1 = AgentA1()
        self.a2 = AgentA2()
        self.a3 = AgentA3()
        self.a4 = AgentA4()
        self.a5 = AgentA5()
        self.events = SSEEventBus(redis_client)
        self.store = StateStore(redis_client)
        self._use_langgraph = self._check_langgraph()

    def set_redis(self, redis_client: Any) -> None:
        self.events._set_redis(redis_client)
        self.store._set_redis(redis_client)

    @staticmethod
    def _check_langgraph() -> bool:
        try:
            import langgraph.graph  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def generate_task_id() -> str:
        today = datetime.now().strftime("%Y%m%d")
        seq = str(uuid.uuid4().int)[:4]
        return f"rca-{today}-{seq}"

    # ------------------------------------------------------------------
    # 状态机构建
    # ------------------------------------------------------------------
    def build_state_machine(self) -> Callable[[RCAState], RCAState]:
        if self._use_langgraph:
            return self._build_langgraph()
        return self.run_sequential

    def _build_langgraph(self) -> Callable[[RCAState], RCAState]:
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(RCAState)

        graph.add_node("A1", self._node_a1)
        graph.add_node("A2", self._node_a2)
        graph.add_node("A3", self._node_a3)
        graph.add_node("A4", self._node_a4)
        graph.add_node("CRAG", self._node_crag)
        graph.add_node("HIL", self._node_hil)
        graph.add_node("A5", self._node_a5)

        graph.add_edge(START, "A1")
        graph.add_edge("A1", "A2")
        graph.add_edge("A1", "A3")
        graph.add_edge("A2", "A4")
        graph.add_edge("A3", "A4")
        graph.add_edge("A4", "CRAG")
        graph.add_edge("CRAG", "HIL")
        graph.add_edge("HIL", "A5")
        graph.add_edge("A5", END)

        graph.set_entry_point("A1")
        compiled = graph.compile()
        return lambda state: compiled.invoke(state)

    # ------------------------------------------------------------------
    # LangGraph 节点
    # ------------------------------------------------------------------
    def _node_a1(self, state: RCAState) -> dict[str, Any]:
        self._publish_stage(state, "A1", "start", {})
        a1_out = self.a1.run(state.bug_info)
        self._publish_stage(state, "A1", "complete", {"suspect_services": a1_out.suspect_services})
        return {
            "symptoms": a1_out.symptoms,
            "error_type": a1_out.error_type,
            "query": a1_out.query,
            "suspect_services": a1_out.suspect_services,
            "stage": Stage(index=1, name="A1", status="completed"),
        }

    def _node_a2(self, state: RCAState) -> dict[str, Any]:
        self._publish_stage(state, "A2", "start", {})
        s_static = self.a2.run(state.suspect_services, state.bug_info.stack)
        self._publish_stage(state, "A2", "complete", {"static_count": len(s_static)})
        return {"S_static": s_static, "stage": Stage(index=2, name="A2", status="completed")}

    def _node_a3(self, state: RCAState) -> dict[str, Any]:
        self._publish_stage(state, "A3", "start", {})
        p_runtime = self.a3.run(state.suspect_services)
        self._publish_stage(state, "A3", "complete", {"runtime_anomaly": p_runtime.runtime_anomaly})
        return {"P_runtime": p_runtime, "stage": Stage(index=2, name="A3", status="completed")}

    def _node_a4(self, state: RCAState) -> dict[str, Any]:
        self._publish_stage(state, "A4", "start", {})
        candidates = cross_validate(state.S_static, state.P_runtime, weights=config.score_weights)
        top3 = self._candidates_to_rootcauses(candidates)
        top_confidence = top3[0].confidence if top3 else 0.0
        self._publish_stage(state, "A4", "complete", {"top_confidence": top_confidence, "top3": [r.model_dump() for r in top3]})
        return {"top3": top3, "stage": Stage(index=4, name="A4", status="completed")}

    def _node_crag(self, state: RCAState) -> dict[str, Any]:
        evidence_list = [self._rootcause_to_candidate(rc) for rc in state.top3]
        triage = crag_gate([c.evidence for c in evidence_list if c is not None])
        status = GateStatus(
            crag=triage.verdict,
            hil=state.gate_status.hil,
        )
        return {"gate_status": status}

    def _node_hil(self, state: RCAState) -> dict[str, Any]:
        candidates = [self._rootcause_to_candidate(rc) for rc in state.top3 if self._rootcause_to_candidate(rc) is not None]
        top_confidence = state.top3[0].confidence if state.top3 else 0.0
        result = hil_gate(candidates, top_confidence, task_id=state.task_id)
        if result.action == "hang" and result.panel_payload:
            if state.runtime_mode == "mock_demo":
                status = GateStatus(crag=state.gate_status.crag, hil="skipped")
                return {"gate_status": status}
            status = GateStatus(crag=state.gate_status.crag, hil="pending")
            self.events.publish(state.task_id, "gate_pending", {
                "gate": "HIL",
                "reason": "low_confidence",
                "top_confidence": top_confidence,
                "payload": result.panel_payload.model_dump(),
            })
            return {"gate_status": status}
        status = GateStatus(crag=state.gate_status.crag, hil="skipped")
        return {"gate_status": status}

    def _node_a5(self, state: RCAState) -> dict[str, Any]:
        self._publish_stage(state, "A5", "start", {})
        solution = self.a5.run(state.top3, state.error_type)
        self._publish_stage(state, "A5", "complete", {"patch_length": len(solution.patch_suggestion)})
        return {"solution": solution, "stage": Stage(index=5, name="A5", status="completed")}

    # ------------------------------------------------------------------
    # 顺序编排（LangGraph 不可用时的兜底实现）
    # ------------------------------------------------------------------
    def run_sequential(self, state: RCAState) -> RCAState:
        try:
            self._ensure_task_id(state)
            self.store.save(state)

            if state.stage.index < 1:
                state = self._apply_a1(state)

            if state.stage.index < 2:
                state = self._apply_a2a3_parallel(state)

            if state.stage.index < 4:
                state = self._apply_a4(state)

            if state.stage.index < 5 or state.gate_status.hil == "pending":
                state = self._apply_gates(state)
                if state.gate_status.hil == "pending":
                    self.store.save(state)
                    return state

            if state.stage.index < 5:
                state = self._apply_a5(state)

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
            try:
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
                flywheel.writeback_sync(payload)
            except Exception:
                pass
            return state
        except RCAError as exc:
            return self._fail(state, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            return self._fail(state, "UNKNOWN", str(exc))

    def run(self, state: RCAState) -> RCAState:
        runner = self.build_state_machine()
        return runner(state)

    # ------------------------------------------------------------------
    # 阶段执行
    # ------------------------------------------------------------------
    def _ensure_task_id(self, state: RCAState) -> None:
        if not state.task_id:
            state.task_id = self.generate_task_id()

    def _apply_a1(self, state: RCAState) -> RCAState:
        self._publish_stage(state, "A1", "start", {})
        a1_out = self.a1.run(state.bug_info)
        state.symptoms = a1_out.symptoms
        state.error_type = a1_out.error_type
        state.query = a1_out.query
        state.suspect_services = a1_out.suspect_services
        state.stage = Stage(index=1, name="A1", status="completed")
        self.store.save(state)
        self._publish_stage(state, "A1", "complete", {"suspect_services": state.suspect_services})
        return state

    def _apply_a2a3_parallel(self, state: RCAState) -> RCAState:
        self._publish_stage(state, "A2A3", "start", {})
        result: dict[str, Any] = {}

        def _a2_worker() -> None:
            result["S_static"] = self.a2.run(state.suspect_services, state.bug_info.stack)

        def _a3_worker() -> None:
            result["P_runtime"] = self.a3.run(state.suspect_services)

        t2 = threading.Thread(target=_a2_worker)
        t3 = threading.Thread(target=_a3_worker)
        t2.start()
        t3.start()
        t2.join()
        t3.join()

        state.S_static = result.get("S_static", [])
        state.P_runtime = result.get("P_runtime", AnomalyPath())
        state.stage = Stage(index=2, name="A2A3", status="completed")
        self.store.save(state)
        self._publish_stage(state, "A2A3", "complete", {
            "static_count": len(state.S_static),
            "runtime_anomaly": state.P_runtime.runtime_anomaly,
        })
        return state

    def _apply_a4(self, state: RCAState) -> RCAState:
        self._publish_stage(state, "A4", "start", {})
        candidates = cross_validate(state.S_static, state.P_runtime, weights=config.score_weights)
        state.top3 = self._candidates_to_rootcauses(candidates)
        top_confidence = state.top3[0].confidence if state.top3 else 0.0
        state.stage = Stage(index=4, name="A4", status="completed")
        self.store.save(state)
        self._publish_stage(state, "A4", "complete", {
            "top_confidence": top_confidence,
            "top3": [r.model_dump() for r in state.top3],
        })
        return state

    def _apply_gates(self, state: RCAState) -> RCAState:
        if state.gate_status.hil in ("pending", "confirmed", "modified", "rejected"):
            return state
        candidates = [self._rootcause_to_candidate(rc) for rc in state.top3]
        valid = [c for c in candidates if c is not None]
        triage = crag_gate([c.evidence for c in valid])
        state.gate_status.crag = triage.verdict

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

    def _apply_a5(self, state: RCAState) -> RCAState:
        self._publish_stage(state, "A5", "start", {})
        solution = self.a5.run(state.top3, state.error_type)
        state.solution = solution
        state.stage = Stage(index=5, name="A5", status="completed")
        self.store.save(state)
        self._publish_stage(state, "A5", "complete", {"patch_length": len(solution.patch_suggestion)})
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

        state.top3 = self._candidates_to_rootcauses(updated)
        state.gate_status.hil = "confirmed" if action == "confirmed" else "modified"
        state.stage = Stage(index=4, name="A4", status="completed")
        self.store.save(state)
        self.events.publish(state.task_id, "gate_resolved", {"gate": "HIL", "action": action})

        result = self.run_sequential(state)
        self.events.publish(result.task_id, "final", {
            "top3": [r.model_dump() for r in result.top3],
            "solution": result.solution.model_dump() if result.solution else {},
            "gate_status": result.gate_status.model_dump(),
        })
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

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
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
