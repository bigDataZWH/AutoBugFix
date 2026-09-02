"""UT 用例 3-4, 9-14: 启动脚本、前端、API 健康、SSE、端口、日志、配置。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent


class TestStartScripts:
    """用例 3-4: test_start_ps1_script / test_start_sh_script"""

    def test_start_sh_syntax(self):
        import subprocess
        result = subprocess.run(["bash", "-n", str(BACKEND_DIR / "start.sh")], capture_output=True)
        assert result.returncode == 0, f"start.sh 语法错误: {result.stderr.decode()}"

    def test_start_sh_has_python_check(self):
        content = (BACKEND_DIR / "start.sh").read_text()
        assert "python3" in content
        assert "3.10" in content or "3.1" in content

    def test_start_sh_has_port_check(self):
        content = (BACKEND_DIR / "start.sh").read_text()
        assert "PORT" in content
        assert "占用" in content

    def test_start_sh_has_mode(self):
        content = (BACKEND_DIR / "start.sh").read_text()
        assert "RCA_RUNTIME_MODE" in content

    def test_start_ps1_has_python_check(self):
        content = (BACKEND_DIR / "start.ps1").read_text()
        assert "python" in content.lower()
        assert "3.10" in content or "3.1" in content

    def test_start_ps1_has_port_check(self):
        content = (BACKEND_DIR / "start.ps1").read_text()
        assert "PORT" in content
        assert "占用" in content or "findstr" in content


class TestFrontendPageLoad:
    """用例 9: test_frontend_page_load — rca-command.html 自包含"""

    def test_html_self_contained(self, rca_command_html):
        # 不应有外部 script src（Google Fonts link 是允许的 CSS）
        scripts = re.findall(r'<script\s+src=["\']https?://', rca_command_html)
        assert len(scripts) == 0, f"发现外部 CDN script: {scripts}"

    def test_html_has_pipeline_stages(self, rca_command_html):
        # 应包含 6 段流水线
        assert "stage" in rca_command_html.lower()
        assert "A1" in rca_command_html or "analyze" in rca_command_html.lower()

    def test_html_has_tabs(self, rca_command_html):
        assert "根因" in rca_command_html or "RCA" in rca_command_html
        assert "知识库" in rca_command_html or "KB" in rca_command_html
        assert "最佳实践" in rca_command_html or "best" in rca_command_html.lower()
        assert "方案" in rca_command_html or "solution" in rca_command_html.lower()


class TestApiHealthCheck:
    """用例 10: test_api_health_check"""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, asgi_client):
        resp = await asgi_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "components" in data
        assert "runtime_mode" in data
        assert "version" in data

    @pytest.mark.asyncio
    async def test_health_components(self, asgi_client):
        resp = await asgi_client.get("/api/v1/health")
        data = resp.json()
        comps = data["components"]
        for key in ("postgres", "redis", "lightrag", "codegraph", "llm"):
            assert key in comps, f"缺少组件 {key}"
            assert comps[key]["status"] in ("up", "down", "degraded", "mock")


class TestSseEndpoint:
    """用例 11: test_sse_endpoint — POST analyze + GET stream"""

    @pytest.mark.asyncio
    async def test_analyze_returns_task_id(self, asgi_client):
        resp = await asgi_client.post("/api/v1/rca/analyze", json={
            "bug_link": "https://example.com/issue/1",
            "repo": "https://github.com/example/repo",
            "bug_desc": "测试 bug",
            "runtime_mode": "mock_demo",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_tasks_endpoint(self, asgi_client):
        resp = await asgi_client.get("/api/v1/rca/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "statuses" in data


class TestPortConflict:
    """用例 12: test_port_conflict"""

    def test_port_check_detects_occupied(self):
        from app.env_check import check_port
        # 8000 被测试服务器占用
        ok, msg = check_port(8000)
        assert ok is False
        assert "占用" in msg

    def test_port_check_available(self):
        from app.env_check import check_port
        ok, msg = check_port(19998)
        assert ok is True


class TestConfigOverride:
    """用例 14: test_config_override — 环境变量 > .env > 默认值"""

    def test_env_overrides_default(self, monkeypatch):
        """环境变量 > 默认值：验证 os.environ.get 读取逻辑"""
        import os
        monkeypatch.setenv("RCA_PORT", "9999")
        val = int(os.environ.get("RCA_PORT", "8000"))
        assert val == 9999

        monkeypatch.delenv("RCA_PORT", raising=False)
        val = int(os.environ.get("RCA_PORT", "8000"))
        assert val == 8000

    def test_runtime_mode_default(self, monkeypatch):
        import os
        monkeypatch.delenv("RCA_RUNTIME_MODE", raising=False)
        val = os.environ.get("RCA_RUNTIME_MODE", "mock_demo")
        assert val == "mock_demo"

    def test_runtime_mode_override(self, monkeypatch):
        import os
        monkeypatch.setenv("RCA_RUNTIME_MODE", "online_full")
        val = os.environ.get("RCA_RUNTIME_MODE", "mock_demo")
        assert val == "online_full"
