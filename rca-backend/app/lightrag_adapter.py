from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .config import config
from .models import AstKg, AstKgEntity, AstKgRelationship, RetrievalResult


class LightRAGAdapter:
    def __init__(self, working_dir: Optional[str] = None) -> None:
        self.working_dir = Path(working_dir or config.lightrag.working_dir).resolve()
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self._rag: Any = None
        self._available = False
        self._init_lightrag()

    def _init_lightrag(self) -> None:
        try:
            from lightrag_hku import LightRAG, QueryParam
            from lightrag_hku.llm import openai_complete_if_cache
            from lightrag_hku.embed import openai_embedding

            async def llm_func(
                prompt: str,
                system_prompt: str | None = None,
                history_messages: list[dict[str, str]] | None = None,
                **kwargs: Any,
            ) -> str:
                model = kwargs.get("model", config.llm.query_model)
                return await openai_complete_if_cache(
                    model=model,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    history_messages=history_messages,
                    base_url=config.llm.base_url,
                    api_key=config.llm.api_key,
                    **kwargs,
                )

            async def embedding_func(texts: list[str]) -> list[list[float]]:
                return await openai_embedding(
                    texts=texts,
                    model=config.embed.model,
                    base_url=config.llm.base_url,
                    api_key=config.embed.api_key or config.llm.api_key,
                )

            self._rag = LightRAG(
                working_dir=str(self.working_dir),
                llm_model_func=llm_func,
                embedding_func=embedding_func,
                embedding_dim=config.embed.dim,
                kv_storage=config.lightrag.kv_storage,
                graph_storage=config.lightrag.graph_storage,
                vector_storage=config.lightrag.vector_storage,
            )
            self._QueryParam = QueryParam
            self._available = True
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def ainsert(self, text: str, ids: Optional[str] = None) -> bool:
        if not self._available:
            return False
        try:
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
        mode_map = {
            "history": "hybrid",
            "propagation": "hybrid",
            "architecture": "high_level",
        }
        mode = mode_map.get(intent, "hybrid")
        return asyncio.run(self.aquery(query, mode=mode))

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