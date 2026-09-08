"""P2-I: Code2CN 测试缺口（I5 缓存边界、I6 解析与分层摘要边界）。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.code2cn import Code2CN
from app.config import config
from app.models import Code2CnRequest, CodeOutline, AstFunctionNode


def _mock_llm(response_json: dict | str = None) -> tuple[Code2CN, MagicMock]:
    """构造带 mock LLM 的 Code2CN 实例。"""
    mock_opencode = MagicMock()
    mock_opencode.model = "qwen2.5-coder"
    if response_json is not None:
        if isinstance(response_json, dict):
            mock_opencode.run_llm.return_value = json.dumps(response_json)
        else:
            mock_opencode.run_llm.return_value = response_json
    return Code2CN(opencode=mock_opencode), mock_opencode


def _basic_response() -> dict:
    return {
        "symbol": "test.fn", "file": "test.py",
        "cn_summary": "1. 参数校验 2. 执行",
        "external_calls": ["DB/users"], "failure_paths": ["TimeoutError"],
        "degraded": False,
    }


# ============================================================================
# I5: 缓存边界
# ============================================================================

class TestI5CacheEdgeCases:
    """I5: 缓存层边界场景。"""

    def test_i5a_cache_miss_first_call(self):
        """首次调用未命中缓存 → cached=False。"""
        cn, mock = _mock_llm(_basic_response())
        req = Code2CnRequest(symbol="fn", file="f.py", source_code="code")
        result = cn.generate(req)
        assert result.cached is False
        assert mock.run_llm.call_count == 1

    def test_i5b_cache_disabled_calls_llm_every_time(self):
        """缓存禁用时每次都调用 LLM，不存储缓存。"""
        cn, mock = _mock_llm(_basic_response())
        req = Code2CnRequest(symbol="fn", file="f.py", source_code="code")
        with patch.object(config.code2cn, "cache_enabled", False):
            r1 = cn.generate(req)
            r2 = cn.generate(req)
        assert r1.cached is False
        assert r2.cached is False
        assert mock.run_llm.call_count == 2

    def test_i5c_same_symbol_different_source_no_false_hit(self):
        """同符号不同源码 → 不同缓存键，不误命中。"""
        cn, mock = _mock_llm({
            "symbol": "fn", "file": "f.py",
            "cn_summary": "版本1", "external_calls": [], "failure_paths": [],
            "degraded": False,
        })
        req1 = Code2CnRequest(symbol="fn", file="f.py", source_code="code_v1")
        req2 = Code2CnRequest(symbol="fn", file="f.py", source_code="code_v2")
        r1 = cn.generate(req1)
        r2 = cn.generate(req2)
        assert r1.cached is False
        assert r2.cached is False
        assert mock.run_llm.call_count == 2

    def test_i5d_cached_outline_returns_same_content(self):
        """缓存命中时返回的内容与首次生成一致。"""
        cn, mock = _mock_llm(_basic_response())
        req = Code2CnRequest(symbol="fn", file="f.py", source_code="code")
        r1 = cn.generate(req)
        r2 = cn.generate(req)
        assert r2.cached is True
        assert r1.cn_summary == r2.cn_summary
        assert r1.symbol == r2.symbol
        assert mock.run_llm.call_count == 1


# ============================================================================
# I6: _parse_response 边界
# ============================================================================

class TestI6ParseResponse:
    """I6: LLM 响应解析边界场景。"""

    def test_i6a_markdown_code_fence_parsing(self):
        """LLM 返回 ```json ... ``` 包裹时正确解析。"""
        raw = '```json\n' + json.dumps(_basic_response()) + '\n```'
        cn, mock = _mock_llm(raw)
        result = cn.generate(Code2CnRequest(symbol="fn", file="f.py", source_code="x"))
        assert result.cn_summary == "1. 参数校验 2. 执行"
        assert result.degraded is False

    def test_i6b_missing_fields_fallback_to_req(self):
        """LLM 返回 JSON 缺少字段时回退到请求参数。"""
        cn, mock = _mock_llm({"cn_summary": "仅摘要"})
        req = Code2CnRequest(symbol="my.fn", file="my.py", source_code="x")
        result = cn.generate(req)
        assert result.symbol == "my.fn"
        assert result.file == "my.py"
        assert result.external_calls == []
        assert result.failure_paths == []

    def test_i6c_invalid_json_triggers_retry(self):
        """LLM 返回非法 JSON → _parse_response 抛异常 → 重试。"""
        cn, mock = _mock_llm()
        mock.run_llm.side_effect = [
            "not valid json at all",
            json.dumps(_basic_response()),
        ]
        cn._max_retry = 3
        result = cn.generate(Code2CnRequest(symbol="fn", file="f.py", source_code="x"))
        assert result.cn_summary == "1. 参数校验 2. 执行"
        assert mock.run_llm.call_count == 2

    def test_i6d_token_stats_recorded_on_success(self):
        """成功生成后 token 统计被记录。"""
        cn, mock = _mock_llm(_basic_response())
        cn.generate(Code2CnRequest(symbol="fn", file="f.py", source_code="x"))
        assert len(cn.token_stats) == 1
        stats = cn.token_stats[0]
        assert stats["role"] == "extract"
        assert stats["model"] == "qwen2.5-coder"
        assert stats["total"] > 0

    def test_i6e_token_stats_not_recorded_on_all_failures(self):
        """全部重试失败后不记录 token 统计。"""
        cn, mock = _mock_llm()
        mock.run_llm.side_effect = Exception("LLM down")
        cn._max_retry = 2
        result = cn.generate(Code2CnRequest(symbol="fn", file="f.py", source_code="x"))
        assert result.degraded is True
        assert len(cn.token_stats) == 0


# ============================================================================
# I6 补充: hierarchical_summary 边界
# ============================================================================

class TestI6HierarchicalSummary:
    """hierarchical_summary 分层摘要边界。"""

    def test_i6f_empty_functions_returns_empty(self):
        """空函数列表 → 返回空列表。"""
        cn, mock = _mock_llm(_basic_response())
        result = cn.hierarchical_summary([])
        assert result == []

    def test_i6g_chunking_branch_triggered(self):
        """函数数 > threshold 时触发分块处理。"""
        cn, mock = _mock_llm(_basic_response())
        nodes = [
            AstFunctionNode(symbol=f"fn{i}", file="f.py", start_line=1, end_line=2, source_code="x")
            for i in range(5)
        ]
        with patch.object(config.code2cn, "hierarchical_threshold", 3), \
             patch.object(config.code2cn, "max_fn_lines", 2):
            result = cn.hierarchical_summary(nodes)
        assert len(result) == 5
        assert mock.run_llm.call_count == 5

    def test_i6h_single_function_no_chunking(self):
        """单个函数 → 直接走 else 分支不分块。"""
        mock_opencode = MagicMock()
        mock_opencode.model = "qwen2.5-coder"
        mock_opencode.run_llm.return_value = json.dumps({
            "symbol": "fn0", "file": "f.py", "cn_summary": "s",
            "external_calls": [], "failure_paths": [], "degraded": False,
        })
        cn = Code2CN(opencode=mock_opencode)
        node = AstFunctionNode(symbol="fn0", file="f.py", start_line=1, end_line=2, source_code="x")
        result = cn.hierarchical_summary([node])
        assert len(result) == 1
        assert result[0].symbol == "fn0"
        assert mock_opencode.run_llm.call_count == 1
