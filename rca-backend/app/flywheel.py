from __future__ import annotations

import json
from typing import Optional

from .config import config
from .lightrag_adapter import lightrag
from .models import (
    FlywheelPayload, SimilarToEdge, WritebackResult,
)


def _hash_payload(payload: FlywheelPayload) -> str:
    material = "|".join([
        payload.root_cause,
        payload.root_cause_function,
        "->".join(payload.call_path),
        payload.fix_patch,
    ])
    return f"ticket:{payload.ticket_id or hash(material) & 0xFFFFFFFF}"


def _looks_duplicate(payload: FlywheelPayload, existing: list[str]) -> bool:
    digest = _hash_payload(payload)
    return digest in existing


class Flywheel:
    def __init__(self) -> None:
        self._inserted_digests: set[str] = set()
        self._similar_edges: list[SimilarToEdge] = []

    async def writeback(self, payload: FlywheelPayload) -> WritebackResult:
        digest = _hash_payload(payload)

        if digest in self._inserted_digests:
            return WritebackResult(inserted=0, similar_edges=[])

        if not lightrag.available:
            self._inserted_digests.add(digest)
            return WritebackResult(inserted=1, similar_edges=[])

        ok = await lightrag.ainsert(
            json.dumps(payload.model_dump(), ensure_ascii=False),
            ids=digest,
        )
        if not ok:
            return WritebackResult(inserted=0, similar_edges=[])

        self._inserted_digests.add(digest)
        edge = SimilarToEdge(
            src_id=digest,
            tgt_id=payload.root_cause_function or digest,
            type="SIMILAR_TO",
            weight=1.0,
        )
        self._similar_edges.append(edge)
        return WritebackResult(inserted=1, similar_edges=[edge])

    def writeback_sync(self, payload: FlywheelPayload) -> WritebackResult:
        import asyncio
        try:
            return asyncio.run(self.writeback(payload))
        except Exception:
            return WritebackResult(inserted=0, similar_edges=[])

    def extract_payload(
        self,
        root_cause: str,
        root_cause_function: str,
        call_path: list[str],
        fix_patch: str,
        verify_case: str,
        ticket_id: str = "",
        title: str = "",
        description: str = "",
    ) -> FlywheelPayload:
        return FlywheelPayload(
            root_cause=root_cause,
            root_cause_function=root_cause_function,
            call_path=call_path,
            fix_patch=fix_patch,
            verify_case=verify_case,
            ticket_id=ticket_id,
            title=title,
            description=description,
        )


flywheel = Flywheel()