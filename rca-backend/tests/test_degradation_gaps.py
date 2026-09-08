"""P2-N: 降级容错测试缺口（N3 无效模式回退、N4 健康端点跨模式）。"""
from __future__ import annotations

import pytest

from app.runtime_mode import (
    get_current_mode, get_profile, is_codegraph_enabled,
    is_lightrag_enabled, is_dual_gate_enabled, check_mode_switch,
    component_status, MATRIX,
)


# ============================================================================
# N3: 无效模式回退
# ============================================================================

class TestN3InvalidModeFallback:
    """N3: 无效/未知模式的降级回退行为。"""

    def test_n3a_invalid_mode_falls_back_to_mock(self):
        """未知模式 → 回退到 mock_demo profile。"""
        p = get_profile("nonexistent_mode")
        assert p.mode == "mock_demo"
        assert p.codegraph == "关闭"

    def test_n3b_empty_string_mode_falls_back_to_mock(self):
        """空字符串模式 → 回退到 mock_demo。"""
        p = get_profile("")
        assert p.mode == "mock_demo"

    def test_n3c_check_mode_switch_invalid_still_requires_restart(self):
        """切换到无效模式仍要求重启（任何非当前模式）。"""
        needs, msg = check_mode_switch("totally_invalid")
        assert needs is True
        assert "需重启" in msg

    def test_n3d_component_status_includes_llm_key(self):
        """component_status 在所有模式下都包含 llm 键。"""
        for mode_name in MATRIX:
            status = component_status()
            assert "llm" in status
            assert "codegraph" in status
            assert "lightrag" in status
            assert "dual_gate" in status

    def test_n3e_component_status_values_are_valid(self):
        """component_status 的值只出现 up/degraded/down/mock。"""
        valid = {"up", "degraded", "down", "mock"}
        for mode_name in MATRIX:
            status = component_status()
            for v in status.values():
                assert v in valid


# ============================================================================
# N4: 健康端点跨模式
# ============================================================================

class TestN4HealthEndpointMockDemo:
    """N4: mock_demo 模式下的健康端点。"""

    @pytest.mark.asyncio
    async def test_n4a_health_mock_demo_status_degraded(self, asgi_client):
        """mock_demo → 整体状态 degraded（codegraph/lightrag/dual_gate 全 down）。"""
        resp = await asgi_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["runtime_mode"] == "mock_demo"
        assert data["version"] == "3.0.0"

    @pytest.mark.asyncio
    async def test_n4b_health_mock_demo_components(self, asgi_client):
        """mock_demo 组件状态：codegraph=down, lightrag=down, llm=mock。"""
        resp = await asgi_client.get("/api/v1/health")
        comps = resp.json()["components"]
        assert comps["codegraph"]["status"] == "down"
        assert comps["lightrag"]["status"] == "down"
        assert comps["llm"]["status"] == "mock"

    @pytest.mark.asyncio
    async def test_n4c_health_has_postgres_and_redis(self, asgi_client):
        """健康端点包含 postgres 和 redis（基础设施组件）。"""
        resp = await asgi_client.get("/api/v1/health")
        comps = resp.json()["components"]
        assert comps["postgres"]["status"] == "up"
        assert comps["redis"]["status"] == "up"


class TestN4HealthEndpointOnlineFull:
    """N4: online_full 模式下的健康端点。"""

    @pytest.mark.asyncio
    async def test_n4d_health_online_full_status_up(self, asgi_client, mode_online_full):
        """online_full → 整体状态 up（所有组件 up）。"""
        resp = await asgi_client.get("/api/v1/health")
        data = resp.json()
        assert data["status"] == "up"
        assert data["runtime_mode"] == "online_full"

    @pytest.mark.asyncio
    async def test_n4e_health_online_full_components_up(self, asgi_client, mode_online_full):
        """online_full 组件状态：codegraph=up, lightrag=up, llm=up。"""
        resp = await asgi_client.get("/api/v1/health")
        comps = resp.json()["components"]
        assert comps["codegraph"]["status"] == "up"
        assert comps["lightrag"]["status"] == "up"
        assert comps["llm"]["status"] == "up"


class TestN4HealthEndpointOfflineLight:
    """N4: offline_light 模式下的健康端点。"""

    @pytest.mark.asyncio
    async def test_n4f_health_offline_status_degraded(self, asgi_client, mode_offline_light):
        """offline_light → 整体状态 degraded（lightrag/dual_gate down）。"""
        resp = await asgi_client.get("/api/v1/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["runtime_mode"] == "offline_light"

    @pytest.mark.asyncio
    async def test_n4g_health_offline_codegraph_degraded(self, asgi_client, mode_offline_light):
        """offline_light → codegraph=degraded, lightrag=down。"""
        resp = await asgi_client.get("/api/v1/health")
        comps = resp.json()["components"]
        assert comps["codegraph"]["status"] == "degraded"
        assert comps["lightrag"]["status"] == "down"


class TestN4LegacyHealthEndpoint:
    """N4: V2 旧版健康端点。"""

    @pytest.mark.asyncio
    async def test_n4h_legacy_health_returns_ok(self, asgi_client):
        """/api/health 返回基础状态。"""
        resp = await asgi_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "opencode_available" in data
        assert "kb_count" in data
        assert data["version"] == "3.0.0"


# ============================================================================
# N4 补充: 模式切换边界
# ============================================================================

class TestN4ModeSwitchEdgeCases:
    """模式切换的边界场景。"""

    def test_n4i_switch_from_online_to_offline(self, mode_online_full):
        """online_full → offline_light 需重启。"""
        needs, msg = check_mode_switch("offline_light")
        assert needs is True
        assert "online_full" in msg
        assert "offline_light" in msg

    def test_n4j_switch_to_same_mode_no_restart(self, mode_offline_light):
        """offline_light → offline_light 不需重启。"""
        needs, msg = check_mode_switch("offline_light")
        assert needs is False
        assert "offline_light" in msg

    def test_n4k_switch_to_invalid_mode_requires_restart(self, mode_mock_demo):
        """切换到无效模式仍要求重启。"""
        needs, msg = check_mode_switch("invalid_xyz")
        assert needs is True
        assert "mock_demo" in msg
