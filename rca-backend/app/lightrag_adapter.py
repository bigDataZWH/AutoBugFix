from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .config import config
from .models import AstKg, AstKgEntity, AstKgRelationship, RetrievalResult

# LightRAG 1.5.6 存储类名映射（config 中存储的是友好名）
# 同一友好名在不同存储类型下对应不同类名
_STORAGE_NAME_MAP: dict[str, dict[str, str]] = {
    "pgvector": {
        "kv": "PGKVStorage",
        "vector": "PGVectorStorage",
        "graph": "PGGraphStorage",
    },
    "json": {
        "kv": "JsonKVStorage",
        "vector": "NanoVectorDBStorage",
        "graph": "NetworkXStorage",
    },
    "redis": {
        "kv": "RedisKVStorage",
        "vector": "PGVectorStorage",
        "graph": "PGGraphStorage",
    },
}


def _resolve_storage_name(name: str, storage_type: str) -> str:
    """解析存储名，兼容友好名和类名两种输入。

    Args:
        name: 配置中的存储名（如 "pgvector"）或完整类名（如 "PGKVStorage"）
        storage_type: 存储类型，'kv' / 'vector' / 'graph'
    """
    lowered = name.lower().strip()
    if lowered in _STORAGE_NAME_MAP:
        return _STORAGE_NAME_MAP[lowered].get(storage_type, name)
    return name


class LightRAGAdapter:
    def __init__(self, working_dir: Optional[str] = None) -> None:
        self.working_dir = Path(working_dir or config.lightrag.working_dir).resolve()
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self._rag: Any = None
        self._available = False
        self._initialized = False
        self._init_lightrag()

    def _init_lightrag(self) -> None:
        try:
            from lightrag import LightRAG, QueryParam
            from lightrag.llm.openai import openai_complete_if_cache
            from lightrag.llm.openai import openai_embed
            from lightrag.utils import wrap_embedding_func_with_attrs

            async def llm_func(
                prompt: str,
                system_prompt: str | None = None,
                history_messages: list[dict[str, str]] | None = None,
                **kwargs: Any,
            ) -> str:
                model = kwargs.get("model", config.llm.query_model)
                if "max_tokens" not in kwargs:
                    prompt_lower = (prompt or "").lower()
                    if "entit" in prompt_lower or "relationship" in prompt_lower:
                        kwargs["max_tokens"] = 16384
                    else:
                        kwargs["max_tokens"] = 8192
                return await openai_complete_if_cache(
                    model=model,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    history_messages=history_messages,
                    base_url=config.llm.base_url,
                    api_key=config.llm.api_key,
                    **kwargs,
                )

            use_local, embed_dim = self._probe_embeddings(openai_embed)

            if use_local:
                import hashlib
                import math
                import numpy as np
                import re as _re

                _TOKEN_RE = _re.compile(
                    r"[A-Za-z_][A-Za-z0-9_]*|\d+|[\u4e00-\u9fa5]+"
                )
                _HASH_DIM = 384

                def _tokenize(text: str) -> list[str]:
                    return _TOKEN_RE.findall((text or "").lower())

                @wrap_embedding_func_with_attrs(
                    embedding_dim=_HASH_DIM,
                    model_name="hashing_384_fallback",
                )
                async def embedding_func(
                    texts: list[str],
                    **kwargs: Any,
                ) -> np.ndarray:
                    results: list[list[float]] = []
                    for text in texts:
                        vec = [0.0] * _HASH_DIM
                        for tok in _tokenize(text):
                            h = hashlib.md5(tok.encode("utf-8")).digest()
                            idx = int.from_bytes(h[:2], "big") % _HASH_DIM
                            sign = 1.0 if (h[2] & 1) else -1.0
                            vec[idx] += sign
                        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
                        results.append([v / norm for v in vec])
                    return np.array(results, dtype=np.float32)
            else:
                @wrap_embedding_func_with_attrs(
                    embedding_dim=embed_dim,
                    model_name=config.embed.model,
                )
                async def embedding_func(
                    texts: list[str],
                    **kwargs: Any,
                ) -> list[list[float]]:
                    return await openai_embed.func(
                        texts=texts,
                        model=config.embed.model,
                        base_url=config.llm.base_url,
                        api_key=config.embed.api_key or config.llm.api_key,
                    )

            self._rag = LightRAG(
                working_dir=str(self.working_dir),
                llm_model_func=llm_func,
                embedding_func=embedding_func,
                kv_storage=_resolve_storage_name(
                    config.lightrag.kv_storage, "kv"
                ),
                graph_storage=_resolve_storage_name(
                    config.lightrag.graph_storage, "graph"
                ),
                vector_storage=_resolve_storage_name(
                    config.lightrag.vector_storage, "vector"
                ),
                entity_extraction_use_json=True,
            )
            self._QueryParam = QueryParam
            self._available = True
        except Exception:
            self._available = False

    def _probe_embeddings(self, openai_embed: Any) -> tuple[bool, int]:
        try:
            import asyncio

            try:
                asyncio.get_running_loop()
                return True, 384
            except RuntimeError:
                pass

            result = asyncio.run(
                asyncio.wait_for(
                    openai_embed.func(
                        texts=["embedding probe test"],
                        model=config.embed.model,
                        base_url=config.llm.base_url,
                        api_key=config.embed.api_key or config.llm.api_key,
                    ),
                    timeout=15.0,
                )
            )
            actual_dim = (
                len(result[0])
                if result and len(result) > 0
                else config.embed.dim
            )
            return False, actual_dim
        except Exception:
            return True, 384

    async def _ensure_storages(self) -> None:
        if not self._initialized:
            init_fn = getattr(self._rag, "initialize_storages", None)
            if init_fn:
                await init_fn()
            self._initialized = True

    @property
    def available(self) -> bool:
        return self._available

    async def ainsert(self, text: str, ids: Optional[str] = None) -> bool:
        if not self._available:
            return False
        try:
            await self._ensure_storages()
            await self._rag.ainsert(text, ids=ids)
            return True
        except Exception:
            return False

    async def ainsert_custom_kg(self, ast_kg: AstKg) -> bool:
        if not self._available:
            return False
        try:
            entities = [
                {"entity_name": e.entity_name, "entity_type": e.type, "description": e.description}
                for e in ast_kg.entities
            ]
            relationships = [
                {
                    "src_id": r.src_id, "tgt_id": r.tgt_id,
                    "description": r.description, "weight": r.weight,
                }
                for r in ast_kg.relationships
            ]
            await self._ensure_storages()
            await self._rag.ainsert_custom_kg(entities, relationships)
            return True
        except Exception:
            return False

    async def aquery(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 60,
    ) -> RetrievalResult:
        if not self._available:
            return RetrievalResult(mode=mode, content="", top_k=top_k, degraded=True, route="lightrag_unavailable")

        start = time.monotonic()
        try:
            await self._ensure_storages()
            param = self._QueryParam(mode=mode, top_k=top_k)
            result = await self._rag.aquery(query, param=param)
            elapsed = int((time.monotonic() - start) * 1000)
            return RetrievalResult(
                mode=mode,
                content=str(result),
                top_k=top_k,
                elapsed_ms=elapsed,
                degraded=False,
                route=f"lightrag_{mode}",
            )
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return RetrievalResult(
                mode=mode,
                content="",
                top_k=top_k,
                elapsed_ms=elapsed,
                degraded=True,
                route=f"lightrag_error:{e}",
            )

    def route_query(self, query: str, intent: str = "history") -> RetrievalResult:
        """三路检索路由：根据 intent 选择检索模式。

        - history: 历史经验匹配（low-level + hybrid）
        - propagation: 根因传播追溯（hybrid，含 CodeGraph 调用边）
        - architecture: 全局架构理解（high_level 主题/社区摘要）
        """
        mode_map = {
            "history": "hybrid",
            "propagation": "hybrid",
            "architecture": "high_level",
        }
        mode = mode_map.get(intent, "hybrid")
        return asyncio.run(self.aquery(query, mode=mode))

    @staticmethod
    def classify_intent(query: str) -> str:
        """基于关键词的意图分类，自动判断三路检索路由。

        历史经验: 含"历史/曾经/上次/类似/相似/之前/经验"
        根因传播: 含"调用链/传播/追溯/链路/上游/下游/谁调用"
        全局架构: 含"架构/模块/整体/全局/概览/结构/设计"
        """
        history_kw = {"历史", "曾经", "上次", "类似", "相似", "之前", "经验", "过去"}
        propagation_kw = {"调用链", "传播", "追溯", "链路", "上游", "下游", "谁调用", "调用关系"}
        architecture_kw = {"架构", "模块", "整体", "全局", "概览", "结构", "设计", "组成"}

        for kw in propagation_kw:
            if kw in query:
                return "propagation"
        for kw in architecture_kw:
            if kw in query:
                return "architecture"
        for kw in history_kw:
            if kw in query:
                return "history"
        return "history"

    def retrieve(
        self,
        query: str,
        intent: str = "history",
        top_k: int = 10,
    ) -> RetrievalResult:
        if not self._available:
            return RetrievalResult(mode="hybrid", content="", top_k=top_k, degraded=True, route="lightrag_unavailable")
        return asyncio.run(self.aquery(query, intent_to_mode(intent), top_k=top_k))


def intent_to_mode(intent: str) -> str:
    return {
        "history": "hybrid",
        "propagation": "hybrid",
        "architecture": "high_level",
        "low_level": "low_level",
    }.get(intent, "hybrid")


lightrag = LightRAGAdapter()