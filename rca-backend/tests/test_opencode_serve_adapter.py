"""opencode serve (headless) HTTP 适配器 UT 测试套件。

覆盖：JSON 提取、SSE 事件消费合并、健康检查降级、错误码、on_event 回调。
不依赖运行中的 opencode serve 实例。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.opencode_serve_adapter import OpenCodeServeAdapter, OpenCodeServeError


class _FakeSSEResponse:
    """模拟 httpx 流式响应，仅实现 ``iter_lines`` 与 ``headers``。"""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code
        self.headers = {"content-type": "text/event-stream"}

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines

    def read(self) -> bytes:
        return "\n".join(self._lines).encode("utf-8")


class TestExtractJson:
    """UT: _extract_json 静态方法"""

    def test_extract_json_dict(self):
        text = '前缀说明\n{"top3": [{"root_cause": "锁竞争"}], "crag_verdict": "relevant"}\n尾部'
        data = OpenCodeServeAdapter._extract_json(text)
        assert isinstance(data, dict)
        assert data["crag_verdict"] == "relevant"
        assert data["top3"][0]["root_cause"] == "锁竞争"

    def test_extract_json_empty_text(self):
        assert OpenCodeServeAdapter._extract_json("") == {}

    def test_extract_json_no_json(self):
        assert OpenCodeServeAdapter._extract_json("没有 JSON 的纯文本") == {}

    def test_extract_json_array_returns_empty(self):
        # 非 dict (数组) 应返回 {}
        assert OpenCodeServeAdapter._extract_json("[1, 2, 3]") == {}

    def test_extract_json_nested(self):
        text = '{"solution": {"diffs": [{"file": "a.py"}], "steps": []}}'
        data = OpenCodeServeAdapter._extract_json(text)
        assert data["solution"]["diffs"][0]["file"] == "a.py"


class TestHealthAndDegrade:
    """UT: 健康检查与降级"""

    def test_unreachable_sets_available_false(self):
        adapter = OpenCodeServeAdapter(base_url="http://127.0.0.1:1", timeout=1)
        assert adapter.available is False

    def test_create_session_unreachable_raises(self):
        adapter = OpenCodeServeAdapter(base_url="http://127.0.0.1:1", timeout=1)
        with pytest.raises(OpenCodeServeError) as exc:
            adapter.create_session("/tmp/repo")
        assert exc.value.code == "SERVE_UNREACHABLE"

    def test_close_session_swallows_error(self):
        adapter = OpenCodeServeAdapter(base_url="http://127.0.0.1:1", timeout=1)
        # 关闭会话失败不应抛异常
        adapter.close_session("sess-001")


class TestOpenCodeServeError:
    """UT: OpenCodeServeError 错误码"""

    def test_error_attributes(self):
        err = OpenCodeServeError("PROMPT_ASYNC", "boom")
        assert err.code == "PROMPT_ASYNC"
        assert err.message == "boom"
        assert str(err) == "boom"


class TestSSEConsume:
    """UT: _consume_sse 事件流消费与消息合并"""

    def test_consume_sse_merges_text_parts(self):
        lines = [
            "event: message.start",
            'data: {"info": {"role": "assistant", "parts": [{"type": "text", "text": "根因是"}]}}',
            "",
            "event: message.delta",
            'data: {"info": {"parts": [{"type": "text", "text": "锁竞争"}]}}',
            "",
            "event: message.completed",
            'data: {"info": {"role": "assistant", "text": "根因是锁竞争"}}',
            "",
        ]
        resp = _FakeSSEResponse(lines)
        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        final = adapter._consume_sse(resp, None)
        assert "锁竞争" in final

    def test_on_event_callback_invoked(self):
        lines = [
            'data: {"info": {"type": "x", "text": "hello"}}',
            "",
        ]
        resp = _FakeSSEResponse(lines)
        received: list[dict[str, Any]] = []
        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        adapter._consume_sse(resp, lambda e: received.append(e))
        assert len(received) == 1
        assert received[0]["info"]["text"] == "hello"

    def test_consume_sse_ignores_done_marker(self):
        lines = [
            'data: {"info": {"parts": [{"type": "text", "text": "ok"}]}}',
            "data: [DONE]",
            "",
        ]
        resp = _FakeSSEResponse(lines)
        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        final = adapter._consume_sse(resp, None)
        assert final == "ok"

    def test_consume_sse_handles_non_json_payload(self):
        lines = [
            "data: not-json-text",
            "",
        ]
        resp = _FakeSSEResponse(lines)
        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        final = adapter._consume_sse(resp, None)
        # 非 JSON payload 不贡献 final_text，但不报错
        assert final == ""
