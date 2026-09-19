"""opencode serve RCA 会话 prompt 模板。

集中管理发给 opencode serve 会话的 prompt：
- ``build_rca_prompt``：主会话 prompt（问题理解→codegraph 分析→Top-3 定位→CRAG 自评迭代→方案生成）。
- ``build_patch_prompt``：HIL ``modify`` 后的轻量补丁会话 prompt（基于确认 top3 重生成 patch/steps）。

CRAG 自评估逻辑（D4）嵌入 prompt：让 LLM 在会话内自行按四维证据打分、判定 verdict、
迭代补强弱维度（最多 ``max_rewrite_rounds`` 轮），避免 Python 层 fallback mock 补强。
"""

from __future__ import annotations

import json
from typing import Any

_JSON_SCHEMA_HINT = """\
最终只输出一个严格 JSON 对象（不要输出 JSON 以外的文字、不要 markdown 代码块）：
{
  "symptoms": ["症状1", "症状2"],
  "error_type": "错误类型，如 STOCK_NEGATIVE",
  "query": "用于检索的查询语句",
  "suspect_services": ["微服务1", "微服务2"],
  "crag_verdict": "relevant | ambiguous | irrelevant",
  "top3": [
    {
      "root_cause": "根因描述",
      "confidence": 0.0~1.0,
      "evidence_chain": ["证据1", "证据2"],
      "located_function": "定位函数全限定名",
      "file": "相对路径",
      "line": 0,
      "evidence": {"static_depth": 0.0, "runtime_anomaly": 0.0, "metric_corr": 0.0, "change_recency": 0.0}
    }
  ],
  "solution": {
    "diffs": [{"file": "", "before": "", "after": "", "summary": ""}],
    "steps": [{"step": 1, "action": "", "detail": ""}],
    "verify_expected": {"key": "value"},
    "patch_suggestion": "",
    "test_cases": ["用例1"]
  }
}"""


def _format_bug(bug_info: Any) -> str:
    if isinstance(bug_info, dict):
        data = bug_info
    else:
        data = {
            "bug_id": getattr(bug_info, "bug_id", ""),
            "title": getattr(bug_info, "title", ""),
            "description": getattr(bug_info, "description", ""),
            "error_type": getattr(bug_info, "error_type", ""),
            "stack": getattr(bug_info, "stack", []),
            "environment": getattr(bug_info, "environment", {}),
            "link": getattr(bug_info, "link", ""),
        }
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_rca_prompt(bug_info: Any, repo_path: str, max_rewrite_rounds: int = 3) -> str:
    """构建 RCA 主会话 prompt。

    指导 opencode 在 ``repo_path`` 代码仓上：
    1. 理解问题症状与错误类型；
    2. 调用 codegraph 工具做真实静态/数据流/热点分析；
    3. 定位 Top-3 根因，给出四维证据评分；
    4. CRAG 自评：按四维证据判定 verdict，弱维度迭代补强（至多 max_rewrite_rounds 轮）；
    5. 生成修复方案（diffs/steps/test_cases）；
    6. 输出严格 JSON。
    """
    return (
        "你是一个资深根因分析（RCA）专家。请在指定代码仓内完成端到端根因分析并输出严格 JSON。\n\n"
        f"【目标代码仓路径】\n{repo_path}\n\n"
        f"【问题工单】\n{_format_bug(bug_info)}\n\n"
        "【分析要求】\n"
        "1. 问题理解：从工单描述/堆栈/错误类型提炼症状、错误类型、检索查询、嫌疑微服务。\n"
        "2. 真实代码分析：使用 codegraph 工具在该代码仓内查询调用结构、数据流与热点函数，"
        "禁止臆测；若无索引则先构建索引。围绕堆栈与症状定位嫌疑函数与调用路径。\n"
        "3. Top-3 根因定位：给出至多 3 个根因候选，每个需定位到具体函数/文件/行，并给出四维证据评分：\n"
        "   - static_depth（静态可达深度）、runtime_anomaly（运行时异常度）、"
        "metric_corr（指标关联度）、change_recency（变更时效度），均 0.0~1.0。\n"
        "4. CRAG 自评估（自行迭代，不要请求外部确认）：\n"
        "   - 根据四维证据均值与完整性判定 verdict：\n"
        "     · relevant（证据充分，四维基本齐全且均分>=0.6）；\n"
        "     · ambiguous（部分维度缺失或偏弱，均分 0.3~0.6）；\n"
        "     · irrelevant（证据不足或方向错误，均分<0.3）。\n"
        f"   - 对 ambiguous/irrelevant，在会话内自主调用 codegraph 工具补强弱维度证据，"
        f"至多重试 {max_rewrite_rounds} 轮；补强后重新判定 verdict。\n"
        "   - 把最终 verdict 写入 crag_verdict 字段。\n"
        "5. 方案生成：基于最终 Top-3 给出修复方案（diffs/steps/verify_expected/test_cases）。\n"
        "6. 严格按以下 schema 输出唯一 JSON 对象，禁止任何 JSON 以外的文字。\n\n"
        f"{_JSON_SCHEMA_HINT}"
    )


def build_patch_prompt(confirmed_top3: list[dict[str, Any]], bug_info: Any) -> str:
    """构建 HIL ``modify`` 后的轻量补丁会话 prompt。

    基于人工确认/调整后的 top3，重新生成修复 patch 与步骤，不重做根因定位。
    """
    top3_text = json.dumps(confirmed_top3, ensure_ascii=False, indent=2)
    return (
        "你是一个修复方案生成助手。人工已确认如下根因 Top-3，请基于此生成可落地的修复方案。\n\n"
        f"【确认的 Top-3 根因】\n{top3_text}\n\n"
        f"【问题工单】\n{_format_bug(bug_info)}\n\n"
        "【要求】\n"
        "1. 针对 Top-3 中置信度最高且 located_function 非空者，生成精准 patch（before/after）与执行步骤。\n"
        "2. 给出验证用例与预期。\n"
        "3. 严格按以下 schema 输出唯一 JSON 对象，禁止任何 JSON 以外的文字。\n\n"
        '{"solution": {\n'
        '  "diffs": [{"file": "", "before": "", "after": "", "summary": ""}],\n'
        '  "steps": [{"step": 1, "action": "", "detail": ""}],\n'
        '  "verify_expected": {"key": "value"},\n'
        '  "patch_suggestion": "",\n'
        '  "test_cases": ["用例1"]\n'
        "}}"
    )
