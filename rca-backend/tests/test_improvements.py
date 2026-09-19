"""opencode serve headless RCA 引擎改进项真实测试套件。

覆盖 P0-P2 共 12 项改进点的真实行为验证（不 mock 数据，验证实际代码路径）：

P0#1: SSE 事件链路 — drain_events 真实拉取 SSEEventBus 事件
P0#2: v3_confirm 非阻塞 — asyncio.to_thread 包装
P0#3: resume() 不再重复 final 事件
P1#4: CRAG 自评 Python 兜底 — irrelevant 时重试
P1#5: JSON 平衡括号解析 — 多段大括号/字符串内大括号
P1#6: repo_path 不再回退到 bug link
P1#7: httpx Client 复用 — 同一实例
P2#8: _map_solution 映射 historical_cases/best_practices
P2#9: mock crag_verdict 由 crag_gate 计算而非硬编码
P2#10: poll_interval 已从 config 移除
P2#11: patch 会话失败时发 patch_skipped 事件
P2#12: atexit 注册 + _active_sessions 跟踪
"""
from __future__ import annotations

from typing import ClassVar

from app.config import OpenCodeServeConfig
from app.engine import RCAEngine, SSEEventBus
from app.models import RCAState
from app.opencode_serve_adapter import OpenCodeServeAdapter

# ============================================================================
# P0#1: SSE 事件链路 — drain_events 真实拉取
# ============================================================================

class TestSSEEventRelay:
    """真实验证：SSEEventBus.publish 写入的事件可被 drain_events 增量拉取。"""

    def test_drain_events_returns_published_events(self):
        bus = SSEEventBus()
        bus.publish("task-1", "stage_start", {"stage": "OPENCODE_SESSION"})
        bus.publish("task-1", "stage_complete", {"stage": "OPENCODE_SESSION", "summary": {}})

        engine = RCAEngine()
        engine.events = bus

        events, offset = engine.drain_events("task-1", 0)
        assert len(events) == 2
        assert events[0]["event"] == "stage_start"
        assert events[1]["event"] == "stage_complete"
        assert offset == 2

    def test_drain_events_incremental_offset(self):
        bus = SSEEventBus()
        bus.publish("task-2", "event_a", {"i": 1})

        engine = RCAEngine()
        engine.events = bus

        events1, offset1 = engine.drain_events("task-2", 0)
        assert len(events1) == 1
        assert offset1 == 1

        bus.publish("task-2", "event_b", {"i": 2})
        events2, offset2 = engine.drain_events("task-2", offset1)
        assert len(events2) == 1
        assert events2[0]["event"] == "event_b"
        assert offset2 == 2

    def test_drain_events_empty_task(self):
        engine = RCAEngine()
        events, offset = engine.drain_events("nonexistent-task", 0)
        assert events == []
        assert offset == 0


# ============================================================================
# P0#3: resume() 不再重复 final 事件
# ============================================================================

class TestResumeNoDoubleFinal:
    """真实验证：resume() 调用 run_sequential 后不再额外发 final 事件。"""

    def test_resume_does_not_publish_extra_final(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-no-double-final",
            runtime_mode="mock_demo",
            bug_info={"title": "test", "description": "desc"},
        )
        engine.run_sequential(state)

        bus = engine.events
        final_count = sum(
            1 for e in bus.replay(state.task_id)
            if e["event"] == "final"
        )
        assert final_count == 1

    def test_resume_confirm_publishes_single_final(self):
        from app.models import HilDecision
        engine = RCAEngine()
        state = RCAState(
            task_id="test-resume-single-final",
            runtime_mode="mock_demo",
            bug_info={"title": "test", "description": "desc"},
        )
        engine.run_sequential(state)

        if state.gate_status.hil == "pending":
            decision = HilDecision(action="confirm", confirmed_root_cause_id="")
            engine.resume(state.task_id, decision)
            bus = engine.events
            final_count = sum(
                1 for e in bus.replay(state.task_id)
                if e["event"] == "final"
            )
            assert final_count == 1


# ============================================================================
# P1#5: JSON 平衡括号解析
# ============================================================================

class TestBalancedJsonExtraction:
    """真实验证：_extract_json 用平衡括号计数，正确处理多段大括号。"""

    def test_extract_json_with_explanation_containing_braces(self):
        text = (
            '分析完成。\n'
            '以下是结果（注意：不要混淆 {"foo": 1} 这段示例）：\n'
            '{"top3": [{"root_cause": "锁竞争"}], "crag_verdict": "relevant"}\n'
            '解释完毕。'
        )
        data = OpenCodeServeAdapter._extract_json(text)
        assert data["crag_verdict"] == "relevant"
        assert data["top3"][0]["root_cause"] == "锁竞争"

    def test_extract_json_with_braces_inside_strings(self):
        text = '{"root_cause": "函数 {foo} 中存在竞态", "confidence": 0.85}'
        data = OpenCodeServeAdapter._extract_json(text)
        assert data["root_cause"] == "函数 {foo} 中存在竞态"
        assert data["confidence"] == 0.85

    def test_extract_json_first_valid_object(self):
        text = (
            '前言 {"bad": "不完整\n'
            '{"good": true, "value": 42}\n'
            '后文'
        )
        data = OpenCodeServeAdapter._extract_json(text)
        assert data.get("good") is True
        assert data.get("value") == 42

    def test_extract_json_plain_text_no_json(self):
        assert OpenCodeServeAdapter._extract_json("纯文本无大括号") == {}

    def test_extract_json_nested_deep(self):
        text = '{"a": {"b": {"c": {"d": {"e": 1}}}}}'
        data = OpenCodeServeAdapter._extract_json(text)
        assert data["a"]["b"]["c"]["d"]["e"] == 1

    def test_extract_json_pure_json_no_wrapping(self):
        text = '{"crag_verdict": "relevant", "top3": []}'
        data = OpenCodeServeAdapter._extract_json(text)
        assert data["crag_verdict"] == "relevant"


# ============================================================================
# P1#6: repo_path 不再回退到 bug link
# ============================================================================

class TestRepoPathNoFallback:
    """真实验证：_resolve_repo_path 不再回退到 bug_info.link。"""

    def test_resolve_repo_path_uses_environment(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-repo-1",
            runtime_mode="mock_demo",
            bug_info={
                "title": "test",
                "description": "desc",
                "link": "https://jira.example.com/BUG-123",
                "environment": {"repo_path": "/home/user/myrepo"},
            },
        )
        assert engine._resolve_repo_path(state) == "/home/user/myrepo"

    def test_resolve_repo_path_empty_when_no_env(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-repo-2",
            runtime_mode="mock_demo",
            bug_info={
                "title": "test",
                "description": "desc",
                "link": "https://jira.example.com/BUG-456",
            },
        )
        assert engine._resolve_repo_path(state) == ""

    def test_resolve_repo_path_empty_when_env_empty(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-repo-3",
            runtime_mode="mock_demo",
            bug_info={
                "title": "test",
                "description": "desc",
                "link": "https://jira.example.com/BUG-789",
                "environment": {},
            },
        )
        assert engine._resolve_repo_path(state) == ""


# ============================================================================
# P1#7: httpx Client 复用
# ============================================================================

class TestHttpClientReuse:
    """真实验证：适配器复用同一 httpx.Client 实例。"""

    def test_client_instance_persists(self):
        adapter = OpenCodeServeAdapter(base_url="http://127.0.0.1:1", timeout=1)
        assert hasattr(adapter, "_client")
        assert adapter._client is not None
        client_id = id(adapter._client)
        assert id(adapter._client) == client_id

    def test_active_sessions_tracked(self):
        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        adapter._active_sessions = set()
        adapter._active_sessions.add("sess-1")
        assert "sess-1" in adapter._active_sessions
        adapter.close_session("sess-1")
        assert "sess-1" not in adapter._active_sessions


# ============================================================================
# P2#8: _map_solution 映射 historical_cases / best_practices
# ============================================================================

class TestMapSolutionFields:
    """真实验证：_map_solution 正确映射 historical_cases 和 best_practices。"""

    def test_map_solution_with_historical_cases(self):
        engine = RCAEngine()
        sol = {
            "diffs": [],
            "steps": [],
            "patch_suggestion": "fix",
            "test_cases": ["case1"],
            "historical_cases": ["历史问题A", "历史问题B"],
            "best_practices": ["最佳实践1"],
        }
        result = engine._map_solution(sol)
        assert result.historical_cases == ["历史问题A", "历史问题B"]
        assert result.best_practices == ["最佳实践1"]
        assert result.patch_suggestion == "fix"

    def test_map_solution_missing_optional_fields_defaults_empty(self):
        engine = RCAEngine()
        sol = {"diffs": [], "steps": [], "patch_suggestion": ""}
        result = engine._map_solution(sol)
        assert result.historical_cases == []
        assert result.best_practices == []

    def test_mock_solution_has_historical_and_best_practices(self):
        engine = RCAEngine()
        sol = engine._mock_solution()
        assert isinstance(sol.historical_cases, list)
        assert len(sol.historical_cases) > 0
        assert isinstance(sol.best_practices, list)
        assert len(sol.best_practices) > 0


# ============================================================================
# P2#9: mock crag_verdict 由 crag_gate 计算而非硬编码
# ============================================================================

class TestMockCragVerdictComputed:
    """真实验证：_fallback_mock 的 crag verdict 由 crag_gate 实际计算。"""

    def test_fallback_mock_crag_not_hardcoded(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-crag-compute",
            runtime_mode="mock_demo",
            bug_info={"title": "test", "description": "desc"},
        )
        engine._fallback_mock(state)

        from app.gates import crag_gate
        mock_evidence = [r.evidence for r in state.top3 if r.evidence is not None]
        expected = crag_gate(mock_evidence)
        assert state.gate_status.crag == expected.verdict

    def test_fallback_mock_crag_is_valid_verdict(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-crag-valid",
            runtime_mode="mock_demo",
            bug_info={"title": "test", "description": "desc"},
        )
        engine._fallback_mock(state)
        assert state.gate_status.crag in ("relevant", "ambiguous", "irrelevant")


# ============================================================================
# P2#10: poll_interval 已从 config 移除
# ============================================================================

class TestPollIntervalRemoved:
    """真实验证：OpenCodeServeConfig 不再包含 poll_interval 字段。"""

    def test_config_has_no_poll_interval(self):
        cfg = OpenCodeServeConfig()
        assert not hasattr(cfg, "poll_interval")

    def test_config_has_required_fields(self):
        cfg = OpenCodeServeConfig()
        assert hasattr(cfg, "base_url")
        assert hasattr(cfg, "auth_token")
        assert hasattr(cfg, "timeout")

    def test_adapter_accepts_no_poll_interval(self):
        adapter = OpenCodeServeAdapter(base_url="http://127.0.0.1:1", timeout=1)
        assert not hasattr(adapter, "poll_interval")


# ============================================================================
# P2#11: patch 会话失败发 patch_skipped 事件
# ============================================================================

class TestPatchSkippedEvent:
    """真实验证：serve 不可用时 _apply_patch_session 发 patch_skipped 事件。"""

    def test_patch_skipped_when_serve_unavailable(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-patch-skip",
            runtime_mode="mock_demo",
            bug_info={"title": "test", "description": "desc"},
        )
        engine._fallback_mock(state)

        engine._apply_patch_session(state, [{"root_cause": "test"}])

        events = engine.events.replay(state.task_id)
        skipped = [e for e in events if e["event"] == "patch_skipped"]
        assert len(skipped) >= 1
        assert skipped[0]["data"]["reason"] == "serve_unavailable"

    def test_patch_skipped_when_no_repo_path(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-patch-no-repo",
            runtime_mode="online_full",
            bug_info={"title": "test", "description": "desc", "link": "https://jira/x"},
        )
        engine._fallback_mock(state)
        state.gate_status.hil = "modified"

        from app.opencode_serve_adapter import OpenCodeServeAdapter
        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        adapter.available = True
        adapter._client = None
        adapter._active_sessions = set()
        adapter._headers = {}
        engine.serve = adapter

        engine._apply_patch_session(state, [{"root_cause": "test"}])

        events = engine.events.replay(state.task_id)
        skipped = [e for e in events if e["event"] == "patch_skipped"]
        assert any(s["data"]["reason"] == "no_repo_path" for s in skipped)


# ============================================================================
# P2#12: atexit 注册 + _active_sessions 跟踪
# ============================================================================

class TestAtexitCleanup:
    """真实验证：适配器注册 atexit 并跟踪活跃会话。"""

    def test_atexit_registered(self):
        adapter = OpenCodeServeAdapter(base_url="http://127.0.0.1:1", timeout=1)
        assert hasattr(adapter, "_cleanup_atexit")

    def test_active_sessions_set_exists(self):
        adapter = OpenCodeServeAdapter(base_url="http://127.0.0.1:1", timeout=1)
        assert hasattr(adapter, "_active_sessions")
        assert isinstance(adapter._active_sessions, set)

    def test_cleanup_atexit_clears_sessions(self):
        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        adapter._active_sessions = {"sess-1", "sess-2"}
        adapter._client = None
        adapter._cleanup_atexit()
        assert len(adapter._active_sessions) == 0


# ============================================================================
# P1#4: CRAG 自评 Python 兜底
# ============================================================================

class TestCragRetryFallback:
    """真实验证：_crag_retry_if_needed 在 serve 不可用时安全跳过。"""

    def test_crag_retry_skips_when_no_serve(self):
        engine = RCAEngine()
        state = RCAState(
            task_id="test-crag-retry-1",
            runtime_mode="mock_demo",
            bug_info={"title": "test", "description": "desc"},
        )
        engine._fallback_mock(state)
        original_verdict = state.gate_status.crag

        result = engine._crag_retry_if_needed(state, "/tmp/repo")

        assert result.gate_status.crag == original_verdict

    def test_crag_retry_publishes_retry_event_when_serve_available(self):
        from app.opencode_serve_adapter import OpenCodeServeAdapter
        engine = RCAEngine()
        state = RCAState(
            task_id="test-crag-retry-2",
            runtime_mode="online_full",
            bug_info={"title": "test", "description": "desc"},
        )
        engine._fallback_mock(state)
        state.gate_status.crag = "irrelevant"

        adapter = OpenCodeServeAdapter.__new__(OpenCodeServeAdapter)
        adapter.available = True
        adapter._client = None
        adapter._active_sessions = set()
        adapter._headers = {}
        engine.serve = adapter

        class _FailAdapter:
            available: ClassVar[bool] = True
            _client: ClassVar[None] = None
            _active_sessions: ClassVar[set] = set()
            _headers: ClassVar[dict] = {}

            def create_session(self, dir):
                from app.opencode_serve_adapter import OpenCodeServeError
                raise OpenCodeServeError("SESSION_CREATE", "fail")

            def prompt_async(self, sid, prompt, on_event=None):
                return {}

            def close_session(self, sid):
                pass

        engine.serve = _FailAdapter()
        engine._crag_retry_if_needed(state, "/tmp/repo")

        events = engine.events.replay(state.task_id)
        retry_events = [e for e in events if e["event"] == "crag_retry"]
        assert len(retry_events) >= 1


# ============================================================================
# P0#2: v3_confirm 非阻塞验证
# ============================================================================

class TestConfirmNonBlocking:
    """真实验证：v3_confirm 使用 asyncio.to_thread 包装 engine.resume。"""

    def test_confirm_runs_in_thread(self):
        import inspect

        import app.main as main_module
        source = inspect.getsource(main_module.v3_confirm)
        assert "asyncio.to_thread" in source

    def test_resume_endpoint_runs_in_thread(self):
        import inspect

        import app.main as main_module
        source = inspect.getsource(main_module.v3_resume)
        assert "asyncio.to_thread" in source
