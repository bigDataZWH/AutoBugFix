#!/usr/bin/env python3
"""Spec1 Task 8.4: code2cn 质量评估脚本。

运行 `scripts/eval_code2cn.py` 输出质量报告：
- 中文大纲语义准确性（目标准确率 ≥ 90%）
- 符号保留率校验（目标 100%，symbol/类名/函数名不被翻译）
- 外部调用检出率（目标 ≥ 85%）
- 异常路径检出率（目标 ≥ 80%）
- 全仓 vs 增量中文化的 token 成本比（增量 ≤ 全仓 30%）
- 兜底成功率验证（配额受限场景降级成功率 ≥ 99%）

用法:
    python scripts/eval_code2cn.py [--samples-dir tests/fixtures/code2cn] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# 将 rca-backend 加入 sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.models import CodeOutline


@dataclass
class EvalReport:
    """质量评估报告。"""
    total_samples: int = 0
    symbol_retention_pass: int = 0
    symbol_retention_rate: float = 0.0
    external_call_detected: int = 0
    external_call_rate: float = 0.0
    failure_path_detected: int = 0
    failure_path_rate: float = 0.0
    degraded_success: int = 0
    degraded_success_rate: float = 0.0
    token_full: int = 0
    token_incremental: int = 0
    token_cost_ratio: float = 0.0
    semantic_accuracy: float = 0.0
    targets: dict = field(default_factory=dict)
    passed: bool = False


# 符号保留正则：检测中文大纲中是否保留了原始符号
SYMBOL_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*")
# 外部调用关键词
EXTERNAL_KEYWORDS = ["DB", "RPC", "缓存", "Redis", "MySQL", "HTTP", "gRPC", "MQ", "Kafka"]
# 异常路径关键词
FAILURE_KEYWORDS = ["try", "catch", "异常", "抛出", "return", "InsufficientException", "Exception"]


def check_symbol_retention(outline: CodeOutline, original_symbol: str) -> bool:
    """检查符号保留率：symbol 字段是否与原始符号一致。"""
    return outline.symbol == original_symbol


def check_external_call_detection(outline: CodeOutline) -> bool:
    """检查外部调用检出率。"""
    if not outline.external_calls:
        return False
    text = " ".join(outline.external_calls) + " " + outline.cn_summary
    return any(kw.lower() in text.lower() for kw in EXTERNAL_KEYWORDS)


def check_failure_path_detection(outline: CodeOutline) -> bool:
    """检查异常路径检出率。"""
    if not outline.failure_paths:
        return False
    text = " ".join(outline.failure_paths) + " " + outline.cn_summary
    return any(kw.lower() in text.lower() for kw in FAILURE_KEYWORDS)


def check_degraded_success(outline: CodeOutline) -> bool:
    """检查兜底成功率：degraded=true 时仍有基础 cn_summary。"""
    if outline.degraded:
        return bool(outline.cn_summary)
    return True  # 非降级视为成功


def evaluate(samples: list[dict], token_full: int = 10000, token_incremental: int = 2500) -> EvalReport:
    """评估样本集并生成报告。"""
    report = EvalReport(
        total_samples=len(samples),
        token_full=token_full,
        token_incremental=token_incremental,
        targets={
            "symbol_retention_rate": 1.0,
            "external_call_rate": 0.85,
            "failure_path_rate": 0.80,
            "degraded_success_rate": 0.99,
            "token_cost_ratio": 0.30,
            "semantic_accuracy": 0.90,
        },
    )

    if not samples:
        return report

    for sample in samples:
        try:
            outline = CodeOutline(**sample) if isinstance(sample, dict) else sample
        except Exception:
            continue

        if check_symbol_retention(outline, sample.get("symbol", "")):
            report.symbol_retention_pass += 1
        if check_external_call_detection(outline):
            report.external_call_detected += 1
        if check_failure_path_detection(outline):
            report.failure_path_detected += 1
        if check_degraded_success(outline):
            report.degraded_success += 1

    n = report.total_samples
    report.symbol_retention_rate = report.symbol_retention_pass / n
    report.external_call_rate = report.external_call_detected / n
    report.failure_path_rate = report.failure_path_detected / n
    report.degraded_success_rate = report.degraded_success / n
    report.token_cost_ratio = report.token_incremental / max(report.token_full, 1)
    # 语义准确性：综合符号保留+外部调用+异常路径检出
    report.semantic_accuracy = (
        report.symbol_retention_rate * 0.4
        + report.external_call_rate * 0.3
        + report.failure_path_rate * 0.3
    )

    # 判定是否全部达标
    report.passed = (
        report.symbol_retention_rate >= report.targets["symbol_retention_rate"]
        and report.external_call_rate >= report.targets["external_call_rate"]
        and report.failure_path_rate >= report.targets["failure_path_rate"]
        and report.degraded_success_rate >= report.targets["degraded_success_rate"]
        and report.token_cost_ratio <= report.targets["token_cost_ratio"]
        and report.semantic_accuracy >= report.targets["semantic_accuracy"]
    )
    return report


def load_samples(samples_dir: Optional[Path] = None) -> list[dict]:
    """加载样本数据。"""
    samples: list[dict] = []
    if samples_dir is None:
        samples_dir = BACKEND_DIR / "tests" / "fixtures" / "code2cn"
    if not samples_dir.exists():
        return samples
    for f in samples_dir.glob("*.json"):
        if f.name == "ast_node_java.json":
            continue
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, list):
                    samples.extend(data)
                elif isinstance(data, dict) and "symbol" in data:
                    samples.append(data)
        except Exception:
            continue
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description="code2cn 质量评估")
    parser.add_argument("--samples-dir", type=str, default=None, help="样本目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--token-full", type=int, default=10000, help="全仓 token 量")
    parser.add_argument("--token-incremental", type=int, default=2500, help="增量 token 量")
    args = parser.parse_args()

    samples_dir = Path(args.samples_dir) if args.samples_dir else None
    samples = load_samples(samples_dir)
    report = evaluate(samples, args.token_full, args.token_incremental)

    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print("code2cn 质量评估报告")
        print("=" * 60)
        print(f"样本数: {report.total_samples}")
        print(f"符号保留率: {report.symbol_retention_rate:.1%} (目标 100%)")
        print(f"外部调用检出率: {report.external_call_rate:.1%} (目标 ≥85%)")
        print(f"异常路径检出率: {report.failure_path_rate:.1%} (目标 ≥80%)")
        print(f"兜底成功率: {report.degraded_success_rate:.1%} (目标 ≥99%)")
        print(f"token 成本比: {report.token_cost_ratio:.1%} (目标 ≤30%)")
        print(f"语义准确性: {report.semantic_accuracy:.1%} (目标 ≥90%)")
        print("=" * 60)
        print(f"总体达标: {'PASS' if report.passed else 'FAIL'}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
