from __future__ import annotations

from typing import Optional

from .config import config, ScoreWeights
from .models import (
    SuspectFunction, AnomalyPath, MetricAnomalies, ChangeRecords,
    Evidence, Candidate, WeightConfig,
)


def functions_of(anomaly_path: AnomalyPath) -> set[str]:
    return set(anomaly_path.functions)


def runtime_anomaly_score(func_id: str, anomaly_path: AnomalyPath) -> float:
    for f in anomaly_path.functions:
        if f == func_id:
            return anomaly_path.runtime_anomaly
    return 0.0


def metric_corr_score(func_id: str, metrics: Optional[MetricAnomalies]) -> float:
    if metrics is None:
        return 0.0
    return metrics.functions.get(func_id, 0.0)


def change_recency_score(func_id: str, changes: Optional[ChangeRecords]) -> float:
    if changes is None:
        return 0.0
    for c in changes.records:
        if c.function_id == func_id:
            if c.timestamp > 0:
                import time as _time
                age_days = max((_time.time() - c.timestamp) / 86400.0, 0.0)
                return max(0.0, 1.0 - age_days / 7.0)
            return min(float(c.commits) / 5.0, 1.0)
    return 0.0


def compute_score(
    func: SuspectFunction,
    anomaly_path: AnomalyPath,
    metrics: Optional[MetricAnomalies],
    changes: Optional[ChangeRecords],
    weights: ScoreWeights,
) -> tuple[float, Evidence]:
    rt = runtime_anomaly_score(func.function_id, anomaly_path)
    mc = metric_corr_score(func.function_id, metrics)
    cr = change_recency_score(func.function_id, changes)

    score = (
        weights.w1 * func.static_depth
        + weights.w2 * rt
        + weights.w3 * mc
        + weights.w4 * cr
    )

    evidence = Evidence(
        static_depth=float(func.static_depth),
        runtime_anomaly=rt,
        metric_corr=mc,
        change_recency=cr,
    )

    return score, evidence


def cross_validate(
    S_static: list[SuspectFunction],
    P_runtime: AnomalyPath,
    metric_anomalies: Optional[MetricAnomalies] = None,
    change_records: Optional[ChangeRecords] = None,
    weights: Optional[ScoreWeights] = None,
) -> list[Candidate]:
    w = weights or config.score_weights
    p_funcs = functions_of(P_runtime)

    intersection_funcs = {f.function_id for f in S_static if f.function_id in p_funcs}
    single_path_funcs = {f.function_id for f in S_static if f.function_id not in p_funcs}

    candidates: list[Candidate] = []

    for func in S_static:
        if func.function_id in intersection_funcs:
            score, evidence = compute_score(func, P_runtime, metric_anomalies, change_records, w)
            candidates.append(Candidate(
                function_id=func.function_id,
                function_name=func.function_name,
                file=func.file,
                line=func.line,
                score=score,
                evidence=evidence,
                hit_kind="intersection",
            ))

    if len(candidates) < 3 and config.score_weights.w1 > 0:
        for func in S_static:
            if func.function_id in single_path_funcs:
                if len(candidates) >= 3:
                    break
                score, evidence = compute_score(func, P_runtime, metric_anomalies, change_records, w)
                candidates.append(Candidate(
                    function_id=func.function_id,
                    function_name=func.function_name,
                    file=func.file,
                    line=func.line,
                    score=score,
                    evidence=evidence,
                    hit_kind="static_only",
                ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:3]


def contains_lookup(
    service_id: str,
    function_map: dict[str, list[SuspectFunction]],
) -> list[SuspectFunction]:
    return function_map.get(service_id, [])


def rebalance_weights(
    weights: ScoreWeights,
    metric_missing: bool = False,
    change_missing: bool = False,
) -> ScoreWeights:
    w = ScoreWeights(
        w1=weights.w1,
        w2=weights.w2,
        w3=0.0 if metric_missing else weights.w3,
        w4=0.0 if change_missing else weights.w4,
    )
    w.normalize()
    return w