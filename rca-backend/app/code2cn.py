from __future__ import annotations

import json
import hashlib
import os
from typing import Optional

from .config import config
from .models import CodeOutline, AstFunctionNode, Code2CnRequest
from .opencode_adapter import OpenCodeAdapter


PROMPT_TEMPLATE = """你是一个代码语义化专家。请为以下函数生成中文大纲。

## 要求
1. 保留原文符号（类名、函数名），symbol 字段不得翻译。
2. 实现逻辑用中文分步描述，每步一句，简洁，编号 1./2./3.…
3. 标注输入参数、返回值、副作用。
4. 涉及外部调用（DB/RPC/缓存）显式标出。
5. 异常路径（try/catch、自定义抛出、提前 return）显式标出。
6. 输出严格 JSON，禁止多余解释文本。

## 函数信息
符号: {symbol}
文件: {file}
签名: {signature}
语言: {language}

## 源码
```{language}
{source_code}
```

## 输出格式
```json
{{
  "symbol": "{symbol}",
  "file": "{file}",
  "cn_summary": "中文分步描述",
  "external_calls": ["DB/库存表", "RPC/库存服务", "缓存/Redis"],
  "failure_paths": ["库存不足抛InsufficientException"],
  "degraded": false
}}
```"""


class Code2CN:
    def __init__(self, opencode: Optional[OpenCodeAdapter] = None) -> None:
        self.opencode = opencode or OpenCodeAdapter(
            binary=config.opencode_binary,
            model=os.environ.get("OPENCODE_MODEL") or config.llm.extract_model,
        )
        self._cache: dict[str, CodeOutline] = {}
        self._token_stats: list[dict] = []
        self._max_retry = config.llm.max_retry if hasattr(config.llm, "max_retry") else 3

    def _record_tokens(self, role: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """记录 token 用量统计。"""
        self._token_stats.append({
            "role": role,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total": prompt_tokens + completion_tokens,
        })

    @property
    def token_stats(self) -> list[dict]:
        return self._token_stats

    def _cache_key(self, symbol: str, source_code: str) -> str:
        return hashlib.md5(f"{symbol}:{source_code}".encode()).hexdigest()

    def _get_from_cache(self, symbol: str, source_code: str) -> Optional[CodeOutline]:
        if not config.code2cn.cache_enabled:
            return None
        key = self._cache_key(symbol, source_code)
        return self._cache.get(key)

    def _set_cache(self, symbol: str, source_code: str, outline: CodeOutline) -> None:
        if not config.code2cn.cache_enabled:
            return
        key = self._cache_key(symbol, source_code)
        self._cache[key] = outline

    def generate(self, req: Code2CnRequest) -> CodeOutline:
        cached = self._get_from_cache(req.symbol, req.source_code)
        if cached:
            cached.cached = True
            return cached

        prompt = PROMPT_TEMPLATE.format(
            symbol=req.symbol,
            file=req.file,
            signature=req.signature or "",
            language=req.language or "python",
            source_code=req.source_code,
        )

        result = None
        for attempt in range(self._max_retry):
            try:
                raw = self.opencode.run_llm(prompt=prompt)
                result = self._parse_response(raw, req)
                # 估算 token 用量（粗略：按字符数/4）
                est_prompt_tokens = len(prompt) // 4
                est_completion_tokens = len(raw) // 4
                self._record_tokens(
                    role="extract",
                    model=self.opencode.model,
                    prompt_tokens=est_prompt_tokens,
                    completion_tokens=est_completion_tokens,
                )
                break
            except Exception:
                if attempt < self._max_retry - 1:
                    continue
                result = CodeOutline(
                    symbol=req.symbol,
                    file=req.file,
                    cn_summary="",
                    external_calls=[],
                    failure_paths=[],
                    degraded=True,
                )

        self._set_cache(req.symbol, req.source_code, result)
        return result

    def _parse_response(self, raw: str, req: Code2CnRequest) -> CodeOutline:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if "```" in cleaned:
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)
        cn_summary = data.get("cn_summary", "")
        if len(cn_summary) > config.code2cn.summary_max_chars:
            cn_summary = cn_summary[:config.code2cn.summary_max_chars]

        return CodeOutline(
            symbol=data.get("symbol", req.symbol),
            file=data.get("file", req.file),
            cn_summary=cn_summary,
            external_calls=data.get("external_calls", []),
            failure_paths=data.get("failure_paths", []),
            degraded=data.get("degraded", False),
        )

    def generate_from_ast(self, node: AstFunctionNode) -> CodeOutline:
        req = Code2CnRequest(
            symbol=node.symbol,
            file=node.file,
            source_code=node.source_code,
            signature=node.signature,
            language=node.language,
        )
        return self.generate(req)

    def hierarchical_summary(self, functions: list[AstFunctionNode]) -> list[CodeOutline]:
        outlines: list[CodeOutline] = []
        if len(functions) > config.code2cn.hierarchical_threshold:
            chunk_size = config.code2cn.max_fn_lines
            for i in range(0, len(functions), chunk_size):
                chunk = functions[i:i + chunk_size]
                for fn in chunk:
                    outlines.append(self.generate_from_ast(fn))
        else:
            for fn in functions:
                outlines.append(self.generate_from_ast(fn))
        return outlines


code2cn = Code2CN()