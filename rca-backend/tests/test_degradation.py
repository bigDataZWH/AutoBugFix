"""UT 用例 5-8: 三级降级矩阵 + 模式切换。"""
from __future__ import annotations

import pytest

from app import runtime_mode
from app.runtime_mode import (
    get_current_mode, get_profile, is_codegraph_enabled,
    is_lightrag_enabled, is_dual_gate_enabled, check_mode_switch,
    component_status, MATRIX,
)


class TestDegradationMatrixOnline:
    """用例 5: test_degradation_matrix_online"""

    def test_online_full_profile(self, mode_online_full):
        p = get_profile()
        assert p.mode == "online_full"
        assert p.codegraph == "完整"
        assert p.lightrag == "完整"
        assert p.dual_gate is True
        assert p.llm_provider == "opencode"

    def test_online_components_up(self, mode_online_full):
        s = component_status()
        assert s["codegraph"] == "up"
        assert s["lightrag"] == "up"
        assert s["dual_gate"] == "up"

    def test_online_flags(self, mode_online_full):
        assert is_codegraph_enabled() is True
        assert is_lightrag_enabled() is True
        assert is_dual_gate_enabled() is True


class TestDegradationMatrixOffline:
    """用例 6: test_degradation_matrix_offline"""

    def test_offline_light_profile(self, mode_offline_light):
        p = get_profile()
        assert p.mode == "offline_light"
        assert p.codegraph == "降级"
        assert p.lightrag == "关闭"
        assert p.dual_gate is False
        assert p.llm_provider == "local_small"
        assert p.fallback_retrieval == "bm25_ripgrep"

    def test_offline_components_degraded(self, mode_offline_light):
        s = component_status()
        assert s["codegraph"] == "degraded"
        assert s["lightrag"] == "down"
        assert s["dual_gate"] == "down"

    def test_offline_flags(self, mode_offline_light):
        assert is_codegraph_enabled() is True  # 降级但启用
        assert is_lightrag_enabled() is False
        assert is_dual_gate_enabled() is False


class TestDegradationMatrixMock:
    """用例 7: test_degradation_matrix_mock"""

    def test_mock_demo_profile(self, mode_mock_demo):
        p = get_profile()
        assert p.mode == "mock_demo"
        assert p.codegraph == "关闭"
        assert p.lightrag == "关闭"
        assert p.dual_gate is False
        assert p.llm_provider == "mock"
        assert p.fallback_retrieval == "mock_data"

    def test_mock_components_all_down(self, mode_mock_demo):
        s = component_status()
        assert s["codegraph"] == "down"
        assert s["lightrag"] == "down"
        assert s["dual_gate"] == "down"
        assert s["llm"] == "mock"

    def test_mock_flags(self, mode_mock_demo):
        assert is_codegraph_enabled() is False
        assert is_lightrag_enabled() is False
        assert is_dual_gate_enabled() is False


class TestModeSwitch:
    """用例 8: test_mode_switch — 运行时切换需重启"""

    def test_switch_requires_restart(self, mode_mock_demo):
        needs_restart, msg = check_mode_switch("online_full")
        assert needs_restart is True
        assert "需重启" in msg

    def test_same_mode_no_switch(self, mode_mock_demo):
        needs_restart, msg = check_mode_switch("mock_demo")
        assert needs_restart is False

    def test_matrix_has_three_modes(self):
        assert len(MATRIX) == 3
        assert "online_full" in MATRIX
        assert "offline_light" in MATRIX
        assert "mock_demo" in MATRIX
