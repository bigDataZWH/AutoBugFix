"""启动前置检查：Python 版本、内存、磁盘、端口占用检测。"""
from __future__ import annotations

import os
import shutil
import socket
import sys


class EnvValidationError(Exception):
    """环境校验失败异常。"""

    def __init__(self, detail: str, exit_code: int = 1):
        self.detail = detail
        self.exit_code = exit_code
        super().__init__(detail)


MIN_PYTHON = (3, 10)
REC_PYTHON = (3, 11)
MIN_MEMORY_GB = 8
REC_MEMORY_GB = 16
MIN_DISK_GB = 2
REC_DISK_GB = 5
DEFAULT_PORT = 8000
CODEGRAPH_PEAK_MEM_GB = 3.2


def check_python_version() -> tuple[bool, str]:
    v = sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < MIN_PYTHON:
        return False, f"Python {ver_str} 低于最低要求 {MIN_PYTHON[0]}.{MIN_PYTHON[1]}，请安装 3.10+"
    if (v.major, v.minor) < REC_PYTHON:
        return True, f"Python {ver_str}（推荐 3.11+）"
    return True, f"Python {ver_str}"


def check_memory() -> tuple[bool, str]:
    try:
        import psutil
        mem = psutil.virtual_memory()
        total_gb = mem.total / (1024 ** 3)
        avail_gb = mem.available / (1024 ** 3)
        if avail_gb < MIN_MEMORY_GB:
            return False, f"可用内存 {avail_gb:.1f}GB 低于最低要求 {MIN_MEMORY_GB}GB，建议切换 RCA_RUNTIME_MODE=offline_light"
        if avail_gb < REC_MEMORY_GB:
            return True, f"可用内存 {avail_gb:.1f}GB（推荐 16GB+）"
        return True, f"可用内存 {avail_gb:.1f}GB"
    except ImportError:
        return True, "psutil 未安装，跳过内存检查"


def check_disk(path: str = ".") -> tuple[bool, str]:
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < MIN_DISK_GB:
            return False, f"剩余磁盘 {free_gb:.1f}GB 低于最低要求 {MIN_DISK_GB}GB"
        if free_gb < REC_DISK_GB:
            return True, f"剩余磁盘 {free_gb:.1f}GB（推荐 5GB SSD）"
        return True, f"剩余磁盘 {free_gb:.1f}GB"
    except Exception:
        return True, "磁盘检查跳过"


def check_port(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> tuple[bool, str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex((host, port))
            if result == 0:
                return False, f"端口 {port} 已被占用，请使用 --port 指定其他端口或终止占用进程"
            return True, f"端口 {port} 可用"
    except Exception:
        return True, f"端口 {port} 检查跳过"


def run_all_checks(port: int = DEFAULT_PORT) -> list[tuple[str, bool, str]]:
    """执行全部前置检查，返回 (检查项, 通过, 描述) 列表。"""
    results: list[tuple[str, bool, str]] = []
    ok, msg = check_python_version()
    results.append(("Python 版本", ok, msg))
    ok, msg = check_memory()
    results.append(("内存", ok, msg))
    ok, msg = check_disk(os.path.dirname(os.path.abspath(__file__)))
    results.append(("磁盘空间", ok, msg))
    ok, msg = check_port(port)
    results.append(("端口", ok, msg))
    return results


def print_checks(port: int = DEFAULT_PORT) -> bool:
    """打印检查结果，返回是否全部通过。"""
    results = run_all_checks(port)
    all_ok = True
    for name, ok, msg in results:
        icon = "[+]" if ok else "[x]"
        print(f"  {icon} {name}: {msg}")
        if not ok:
            all_ok = False
    if not all_ok:
        print("\n环境校验未通过，请修复上述问题后重试。")
    return all_ok


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RCA 环境前置检查")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    ok = print_checks(args.port)
    sys.exit(0 if ok else 1)
