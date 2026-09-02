"""UT 用例 1-2: 环境校验 + 依赖安装检查。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.env_check import check_python_version, check_port, check_memory, check_disk, EnvValidationError


class TestEnvValidation:
    """用例 1: test_env_validation"""

    def test_python_version_ok(self):
        ok, msg = check_python_version()
        assert ok is True
        assert "Python" in msg

    def test_python_version_below_minimum(self, monkeypatch):
        class FakeVI:
            major, minor, micro = 3, 9, 5
        monkeypatch.setattr(sys, "version_info", FakeVI())
        ok, msg = check_python_version()
        assert ok is False
        assert "低于" in msg

    def test_port_available(self):
        ok, msg = check_port(19999)  # 使用一个几乎肯定空闲的端口
        assert ok is True

    def test_port_occupied(self):
        # 8000 端口已在运行服务
        ok, msg = check_port(8000)
        # 如果端口被占用，应该返回 False
        assert ok is False
        assert "占用" in msg

    def test_memory_check(self):
        ok, msg = check_memory()
        assert ok is True

    def test_disk_check(self, tmp_path):
        ok, msg = check_disk(str(tmp_path))
        assert ok is True


class TestRequirementsInstall:
    """用例 2: test_requirements_install — 检查 requirements.txt 格式与可解析性"""

    def test_requirements_parseable(self):
        req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
        lines = req_path.read_text().splitlines()
        # 去掉注释和空行
        deps = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
        assert len(deps) > 0
        for dep in deps:
            assert "==" in dep or ">=" in dep, f"依赖 {dep} 缺少版本约束"

    def test_core_deps_present(self):
        req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
        content = req_path.read_text()
        for pkg in ["fastapi", "uvicorn", "pydantic", "chromadb", "networkx", "httpx"]:
            assert pkg in content.lower(), f"核心依赖 {pkg} 不在 requirements.txt"
