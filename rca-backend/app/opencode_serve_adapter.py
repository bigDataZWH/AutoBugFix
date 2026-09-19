"""opencode serve (headless) HTTP 适配器。

封装 opencode serve 长驻 server 的会话生命周期与 SSE 事件流消费。
与 ``opencode_adapter.OpenCodeAdapter``（per-call ``opencode run`` 子进程模式）分工：
- 本适配器：V3 RCAEngine 单会话编排（分析+CRAG自评+方案），HTTP + SSE
- OpenCodeAdapter：V2 Pipeline 代码分析 + 云捷取票，子进程模式

serve 不可用时 ``available=False``，调用方据此降级到 mock_demo 路径。
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OpenCodeServeError(Exception):
    """opencode serve 适配器错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OpenCodeServeAdapter:
    """opencode serve HTTP 客户端。

    典型流程::

        adapter = OpenCodeServeAdapter(base_url, auth_token, timeout)
        sid = adapter.create_session(repo_path)
        result = adapter.prompt_async(sid, prompt, on_event=bus_cb)
        adapter.close_session(sid)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:4096",
        auth_token: str = "",
        timeout: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
        self._client: httpx.Client = httpx.Client(
            base_url=self.base_url,
            headers=self._headers,
            timeout=self.timeout,
        )
        self._active_sessions: set[str] = set()
        self.available = self._health()
        atexit.register(self._cleanup_atexit)

    def _health(self) -> bool:
        try:
            r = self._client.get("/health")
            return r.status_code < 500
        except Exception as e:  # noqa: BLE001
            logger.info("opencode serve 不可用，降级 mock: %s", e)
            return False

    def create_session(self, directory: str) -> str:
        headers = dict(self._headers)
        headers["x-opencode-directory"] = directory
        try:
            r = self._client.post("/session", params={"directory": directory}, headers=headers)
        except Exception as e:
            raise OpenCodeServeError("SERVE_UNREACHABLE", f"创建会话失败: {e}") from e
        if r.status_code >= 400:
            raise OpenCodeServeError("SESSION_CREATE", f"创建会话 HTTP {r.status_code}: {r.text}")
        data = self._safe_json(r)
        sid = data.get("id") or data.get("sessionID") or data.get("session_id")
        if not sid:
            raise OpenCodeServeError("SESSION_CREATE", f"会话响应缺 id 字段: {data}")
        sid_str = str(sid)
        self._active_sessions.add(sid_str)
        return sid_str

    def prompt_async(
        self,
        session_id: str,
        prompt: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        url = f"/session/{session_id}/prompt_async"
        body = {"prompt": prompt, "stream": True}
        final_text = ""
        try:
            with self._client.stream("POST", url, json=body) as resp:
                if resp.status_code >= 400:
                    text = resp.read().decode("utf-8", "replace")
                    raise OpenCodeServeError("PROMPT_ASYNC", f"prompt_async HTTP {resp.status_code}: {text}")
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" in ctype or "event-stream" in ctype:
                    final_text = self._consume_sse(resp, on_event)
                else:
                    raw = resp.read().decode("utf-8", "replace")
                    data = self._safe_json_text(raw)
                    if isinstance(data, dict):
                        self._emit(on_event, data)
                        final_text = self._extract_final_text(data)
        except OpenCodeServeError:
            raise
        except Exception as e:
            raise OpenCodeServeError("SERVE_TIMEOUT", f"消费会话事件失败: {e}") from e

        parsed = self._extract_json(final_text)
        if parsed:
            return parsed
        return {"_raw": final_text} if final_text else {}

    def _consume_sse(
        self,
        resp: httpx.Response,
        on_event: Callable[[dict[str, Any]], None] | None,
    ) -> str:
        final_text = ""
        current_type: str | None = None
        for line in resp.iter_lines():
            if not isinstance(line, str):
                line = str(line)
            if line == "":
                current_type = None
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                current_type = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            evt = self._safe_json_text(payload)
            if not isinstance(evt, dict):
                evt = {"raw": payload}
            evt.setdefault("event", current_type)
            self._emit(on_event, evt)
            final_text = self._merge_message(evt, final_text)
        return final_text

    @staticmethod
    def _emit(on_event: Callable[[dict[str, Any]], None] | None, evt: dict[str, Any]) -> None:
        if on_event is None:
            return
        try:
            on_event(evt)
        except Exception as e:  # noqa: BLE001
            logger.warning("on_event 回调异常（已忽略）: %s", e)

    @staticmethod
    def _merge_message(evt: dict[str, Any], final_text: str) -> str:
        raw_info = evt.get("info")
        info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else evt
        etype = evt.get("event") or info.get("type")
        if isinstance(info.get("parts"), list):
            for p in info["parts"]:
                if isinstance(p, dict) and p.get("type") == "text":
                    final_text += p.get("text", "")
        if info.get("role") == "assistant" and isinstance(info.get("text"), str):
            final_text = info["text"]
        if etype in {"message.completed", "message.complete", "session.completed"} and isinstance(info.get("text"), str):
            final_text = info["text"]
        if isinstance(info.get("output"), str) and info.get("output"):
            final_text = info["output"]
        return final_text

    @staticmethod
    def _extract_final_text(data: dict[str, Any]) -> str:
        if isinstance(data.get("output"), str):
            return data["output"]
        raw_msg = data.get("message")
        msg: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else data
        if isinstance(msg.get("parts"), list):
            for p in msg["parts"]:
                if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str):
                    return p["text"]
        if isinstance(msg.get("text"), str):
            return msg["text"]
        return ""

    def close_session(self, session_id: str) -> None:
        self._active_sessions.discard(session_id)
        try:
            self._client.delete(f"/session/{session_id}")
        except Exception as e:  # noqa: BLE001
            logger.debug("关闭会话 %s 失败（已忽略）: %s", session_id, e)

    def _cleanup_atexit(self) -> None:
        for sid in list(self._active_sessions):
            with contextlib.suppress(Exception):
                self._client.delete(f"/session/{sid}")
        self._active_sessions.clear()
        with contextlib.suppress(Exception):
            self._client.close()

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict[str, Any]:
        return OpenCodeServeAdapter._safe_json_text(resp.text)

    @staticmethod
    def _safe_json_text(text: str) -> Any:
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """从 LLM 输出文本中提取 JSON 对象。

        优先尝试直接解析整段文本；失败后用字符串感知的平衡括号计数遍历
        每一个 ``{`` 起点，收集所有可解析的候选对象，返回最长（最完整）的那个。
        这样既能跳过解释文本中出现的小段示例 JSON（如 ``{"foo": 1}``），
        也能在首段大括号因字符串未闭合而无法解析时回退到后续有效对象，
        避免贪婪正则 ``\\{[\\s\\S]*\\}`` 或“首个命中即返回”策略误捕获。
        """
        if not text:
            return {}
        text = text.strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass

        best: dict[str, Any] | None = None
        best_len = -1
        n = len(text)
        for start in range(n):
            if text[start] != "{":
                continue
            depth = 0
            in_string = False
            escape = False
            for i in range(start, n):
                ch = text[i]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = text[start:i + 1]
                            try:
                                data = json.loads(candidate)
                            except (json.JSONDecodeError, ValueError):
                                data = None
                            if (
                                isinstance(data, dict)
                                and len(candidate) > best_len
                            ):
                                best = data
                                best_len = len(candidate)
                            break
        return best if best is not None else {}
