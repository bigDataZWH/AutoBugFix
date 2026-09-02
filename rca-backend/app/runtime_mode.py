"""运行模式降级矩阵：根据 RCA_RUNTIME_MODE 控制各组件开关。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

from .config import config, RuntimeMode


@dataclass
class DegradationProfile:
    """降级矩阵档位描述。"""
    mode: str
    codegraph: str       # "完整" | "降级" | "关闭"
    lightrag: str         # "完整" | "降级" | "关闭"
    dual_gate: bool       # 双图谱交叉验证开关
    llm_provider: str     # "opencode" | "local_small" | "mock"
    fallback_retrieval: str  # "dual_graph_cross_validate" | "bm25_ripgrep" | "mock_data"
    env_required: str     # "recommended" | "minimum"


MATRIX: dict[str, DegradationProfile] = {
    "online_full": DegradationProfile(
        mode="online_full",
        codegraph="完整",
        lightrag="完整",
        dual_gate=True,
        llm_provider="opencode",
        fallback_retrieval="dual_graph_cross_validate",
        env_required="recommended",
    ),
    "offline_light": DegradationProfile(
        mode="offline_light",
        codegraph="降级",
        lightrag="关闭",
        dual_gate=False,
        llm_provider="local_small",
        fallback_retrieval="bm25_ripgrep",
        env_required="minimum",
    ),
    "mock_demo": DegradationProfile(
        mode="mock_demo",
        codegraph="关闭",
        lightrag="关闭",
        dual_gate=False,
        llm_provider="mock",
        fallback_retrieval="mock_data",
        env_required="minimum",
    ),
}

# 运行时当前模式（不支持热切换，需重启）
_current_mode: Optional[str] = None


def get_current_mode() -> str:
    global _current_mode
    if _current_mode is None:
        _current_mode = os.environ.get("RCA_RUNTIME_MODE", "mock_demo")
    return _current_mode


def get_profile(mode: Optional[str] = None) -> DegradationProfile:
    m = mode or get_current_mode()
    return MATRIX.get(m, MATRIX["mock_demo"])


def is_codegraph_enabled() -> bool:
    p = get_profile()
    return p.codegraph != "关闭"


def is_lightrag_enabled() -> bool:
    p = get_profile()
    return p.lightrag != "关闭"


def is_dual_gate_enabled() -> bool:
    return get_profile().dual_gate


def check_mode_switch(new_mode: str) -> tuple[bool, str]:
    """检查模式切换是否需要重启。返回 (是否需重启, 提示消息)。"""
    current = get_current_mode()
    if new_mode == current:
        return False, f"当前已为 {new_mode} 模式"
    return True, f"模式切换 {current} → {new_mode} 需重启服务生效，不热切换以避免图谱状态不一致"


def component_status() -> dict[str, str]:
    """返回各组件在当前模式下的状态。"""
    p = get_profile()
    def map_status(s: str) -> str:
        if s == "完整": return "up"
        if s == "降级": return "degraded"
        return "down"
    return {
        "codegraph": map_status(p.codegraph),
        "lightrag": map_status(p.lightrag),
        "llm": "up" if p.llm_provider != "mock" else "mock",
        "dual_gate": "up" if p.dual_gate else "down",
    }
