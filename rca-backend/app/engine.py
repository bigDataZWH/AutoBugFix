"""Spec 4: 5-Agent 智能引擎编排器。

拓扑: START->A1->(A2||A3 fan-out)->A4(cross_validate)->CRAG->HIL->A5->END。
SSE 事件推送 + Redis 状态持久化 + 断点续跑 + HIL 挂起/回灌。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional

from .agents import AgentA1, AgentA2, AgentA3, AgentA5, RCAError
from .config import config
from .dual_graph import cross_validate
from .flywheel import flywheel
from .gates import crag_gate, hil_gate, process_hil_decision
from .lightrag_adapter import lightrag
from .models import (
    A1Output, AnomalyPath, BugInfo, Candidate, ChangeRecord, ChangeRecords,
    CragTriage, Evidence, GateStatus, HilDecision, HilResult, MetricAnomalies,
    RCAState, RootCause, Solution, Stage, SuspectFunction,
)

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
    """5-Agent 引擎编排入口。"""

    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self.a1 = AgentA1()
        self.a2 = AgentA2()
        self.a3 = AgentA3()
        self.a5 = AgentA5()
        self.events = SSEEventBus(redis_client)
        self.store = StateStore(redis_client)

    def set_redis(self, redis_client: Any) -> None:
        self.events._set_redis(redis_client)
        self.store._set_redis(redis_client)

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

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a2 = pool.submit(self.a2.run, state.suspect_services, state.bug_info.stack)
            future_a3 = pool.submit(self.a3.run, state.suspect_services)
            state.S_static = future_a2.result()
            state.P_runtime = future_a3.result()

        state.metric_anomalies, state.change_records = self._collect_supplementary_data(state)
        state.stage = Stage(index=2, name="A2A3", status="completed")
        self.store.save(state)
        self._publish_stage(state, "A2A3", "complete", {
            "static_count": len(state.S_static),
            "runtime_anomaly": state.P_runtime.runtime_anomaly,
            "has_metrics": state.metric_anomalies is not None,
            "has_changes": state.change_records is not None,
        })
        return state

    def _apply_a4(self, state: RCAState) -> RCAState:
        self._publish_stage(state, "A4", "start", {})
        candidates = cross_validate(
            state.S_static,
            state.P_runtime,
            metric_anomalies=state.metric_anomalies,
            change_records=state.change_records,
            weights=config.score_weights,
        )
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
        rewrite_rounds = 0

        while triage.verdict != "relevant" and rewrite_rounds < config.gate.max_rewrite_rounds:
            rewrite_rounds += 1
            hint = triage.rewritten_query or ""
            logger.info(
                "CRAG rewrite round %d/%d: verdict=%s hint=%s task_id=%s",
                rewrite_rounds, config.gate.max_rewrite_rounds,
                triage.verdict, hint, state.task_id,
            )
            supplemented, did_supplement = self._supplement_evidence(state, hint)
            if not did_supplement:
                break
            valid = supplemented
            state.top3 = self._candidates_to_rootcauses(valid)
            triage = crag_gate([c.evidence for c in valid if c is not None])

        state.gate_status.crag = triage.verdict
        if rewrite_rounds > 0:
            logger.info(
                "CRAG rewrite 完成: rounds=%d final_verdict=%s task_id=%s",
                rewrite_rounds, triage.verdict, state.task_id,
            )

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
    def _collect_supplementary_data(
        self, state: RCAState
    ) -> tuple[Optional[MetricAnomalies], Optional[ChangeRecords]]:
        """采集指标异常 + 变更记录，供 cross_validate 的 w3/w4 维度使用。

        mock_demo 模式：基于 P_runtime.functions 生成模拟数据。
        online_full / offline_light：暂无监控/CI 集成，返回 None（降级）。
        """
        func_ids = state.P_runtime.functions or [f.function_id for f in state.S_static]
        if not func_ids:
            return None, None

        if state.runtime_mode != "mock_demo":
            logger.info("supplementary data 跳过（非 mock_demo 模式）: task_id=%s", state.task_id)
            return None, None

        metrics = self._generate_fallback_metrics(state, func_ids)
        changes = self._generate_fallback_changes(state, func_ids)

        logger.debug(
            "supplementary data 采集完成: task_id=%s metrics_funcs=%d change_records=%d",
            state.task_id, len(metrics.functions), len(changes.records),
        )
        return metrics, changes

    def _generate_fallback_metrics(
        self, state: RCAState, func_ids: list[str]
    ) -> MetricAnomalies:
        """生成兜底指标异常数据（mock / CRAG 重写补充场景）。"""
        return MetricAnomalies(
            functions={fid: round(0.60 + 0.30 * (1.0 - i * 0.15), 2) for i, fid in enumerate(func_ids[:5])},
            services={svc: 0.80 for svc in state.suspect_services[:3]},
        )

    def _generate_fallback_changes(
        self, state: RCAState, func_ids: list[str]
    ) -> ChangeRecords:
        """生成兜底变更记录（mock / CRAG 重写补充场景）。"""
        now = time.time()
        return ChangeRecords(records=[
            ChangeRecord(function_id=fid, timestamp=now - 2 * 86400, commits=3 + i)
            for i, fid in enumerate(func_ids[:5])
        ])

    def _supplement_evidence(
        self, state: RCAState, hint: str
    ) -> tuple[list[Candidate], bool]:
        """根据 CRAG 重写提示补充弱维度证据，重新 cross_validate。

        hint="broaden": 全维度补充（irrelevant 场景）。
        hint 含 "metric_corr": 补充指标关联数据。
        hint 含 "change_recency": 补充变更时效数据。
        返回 (补充后的候选列表, 是否实际补充)。
        """
        func_ids = state.P_runtime.functions or [f.function_id for f in state.S_static]
        if not func_ids:
            return [], False

        need_metrics = hint == "broaden" or "metric_corr" in hint
        need_changes = hint == "broaden" or "change_recency" in hint

        metrics = state.metric_anomalies
        changes = state.change_records
        supplemented = False

        if need_metrics and (metrics is None or not metrics.functions):
            metrics = self._generate_fallback_metrics(state, func_ids)
            supplemented = True
            logger.info(
                "CRAG 补充 metric_corr: funcs=%d task_id=%s",
                len(metrics.functions), state.task_id,
            )
        if need_changes and (changes is None or not changes.records):
            changes = self._generate_fallback_changes(state, func_ids)
            supplemented = True
            logger.info(
                "CRAG 补充 change_recency: records=%d task_id=%s",
                len(changes.records), state.task_id,
            )

        if not supplemented:
            return [], False

        candidates = cross_validate(
            state.S_static, state.P_runtime,
            metric_anomalies=metrics, change_records=changes,
            weights=config.score_weights,
        )
        return candidates, True

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
