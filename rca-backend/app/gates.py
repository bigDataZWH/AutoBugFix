from __future__ import annotations

from typing import Optional

from .config import config
from .models import (
    Candidate, CragTriage, PanelPayload, HilResult, HilDecision,
    Evidence,
)


def crag_gate(evidence_list: list[Evidence]) -> CragTriage:
    if not evidence_list:
        return CragTriage(
            verdict="irrelevant",
            refined_evidence=[],
            augmented_query=None,
            rewritten_query="",
        )

    avg_confidence = 0.0
    total_dims = 0
    for ev in evidence_list:
        dims = [ev.static_depth, ev.runtime_anomaly, ev.metric_corr, ev.change_recency]
        for d in dims:
            if d > 0:
                avg_confidence += d
                total_dims += 1

    if total_dims > 0:
        avg_confidence /= total_dims
    else:
        avg_confidence = 0.0

    if avg_confidence >= config.gate.confidence_threshold:
        refined = [ev.model_dump() for ev in evidence_list if any([
            ev.static_depth > 0, ev.runtime_anomaly > 0,
            ev.metric_corr > 0, ev.change_recency > 0,
        ])]
        return CragTriage(
            verdict="relevant",
            refined_evidence=refined,
            augmented_query=None,
            rewritten_query=None,
        )
    elif avg_confidence >= config.gate.confidence_threshold * 0.5:
        return CragTriage(
            verdict="ambiguous",
            refined_evidence=[ev.model_dump() for ev in evidence_list],
            augmented_query="",
            rewritten_query=None,
        )
    else:
        return CragTriage(
            verdict="irrelevant",
            refined_evidence=[],
            augmented_query=None,
            rewritten_query="",
        )


def hil_gate(
    top3: list[Candidate],
    confidence: float,
    task_id: str = "",
) -> HilResult:
    threshold = config.gate.hil_confidence_threshold

    if confidence >= threshold:
        return HilResult(action="pass", panel_payload=None)

    return HilResult(
        action="hang",
        panel_payload=PanelPayload(
            task_id=task_id,
            top3=[c.model_dump() for c in top3],
            confidence=confidence,
            threshold=threshold,
        ),
    )


def structure_gate(top3: list[Candidate]) -> bool:
    for c in top3:
        if not c.function_name:
            return False
    return True


def semantic_gate(top3: list[Candidate]) -> bool:
    for c in top3:
        if c.score <= 0:
            return False
    return True


def process_hil_decision(
    decision: HilDecision,
    top3: list[Candidate],
) -> tuple[list[Candidate], str]:
    if decision.action == "confirm":
        return top3, "confirmed"
    elif decision.action == "modify":
        if decision.modified_top3:
            converted = []
            for item in decision.modified_top3:
                if isinstance(item, Candidate):
                    converted.append(item)
                elif isinstance(item, dict):
                    if "function_id" in item or "function_name" in item:
                        converted.append(Candidate.model_validate(item))
                    else:
                        name = item.get("located_function") or item.get("root_cause", "")
                        converted.append(Candidate(
                            function_id=name,
                            function_name=name,
                            file=item.get("file", ""),
                            line=item.get("line", 0),
                            score=item.get("confidence") or item.get("score", 0.0),
                        ))
            return converted, "modified"
        picked = getattr(decision, "confirmed_root_cause_id", "") or ""
        if picked and top3:
            matched = [c for c in top3 if c.function_name == picked or c.function_id == picked]
            others = [c for c in top3 if not (c.function_name == picked or c.function_id == picked)]
            return matched + others, "modified"
        return top3, "modified"
    elif decision.action == "reject":
        return [], "rejected"
    return top3, "confirmed"