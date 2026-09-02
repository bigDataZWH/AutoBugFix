from __future__ import annotations
import hashlib
import math
import re
from typing import Optional

import chromadb
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
from rank_bm25 import BM25Okapi

from .models import KBImportItem, KBMatch


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[\u4e00-\u9fa5]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class HashingEmbeddingFunction(EmbeddingFunction):
    DIM = 384

    def __call__(self, input: Documents) -> Embeddings:
        return [self._embed(d) for d in input]

    def name(self) -> str:
        return "hashing_384"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for tok in tokenize(text):
            h = hashlib.md5(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:2], "big") % self.DIM
            sign = 1.0 if (h[2] & 1) else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class Retriever:
    def __init__(self, path: str = "./data/chroma"):
        self.path = path
        self.ef = HashingEmbeddingFunction()
        self.client = chromadb.PersistentClient(path=path)
        self.col = self.client.get_or_create_collection(
            "tickets", embedding_function=self.ef, metadata={"hnsw:space": "cosine"}
        )
        self._docs: list[str] = []
        self._metas: list[dict] = []
        self._ids: list[str] = []
        self._bm25: Optional[BM25Okapi] = None
        self._rebuild_bm25()

    def count(self) -> int:
        return self.col.count()

    def import_tickets(self, items: list[KBImportItem]) -> int:
        if not items:
            return 0
        docs, metas, ids = [], [], []
        for it in items:
            doc = f"{it.title}\n{it.description}\n根因:{it.root_cause}\n修复:{it.fix_code}"
            meta = {
                "ticket_id": it.ticket_id,
                "title": it.title,
                "root_cause": it.root_cause,
                "fix_code": it.fix_code,
                "microservice": it.microservice or "",
                "module": it.module or "",
                "error_code": it.error_code or "",
                "severity": it.severity or "",
            }
            docs.append(doc)
            metas.append(meta)
            ids.append(it.ticket_id)
        self.col.upsert(documents=docs, metadatas=metas, ids=ids)
        self._reload_all()
        return len(items)

    def seed_if_empty(self, seeds: list[dict]) -> int:
        if self.col.count() > 0:
            return 0
        items = [KBImportItem(**s) for s in seeds]
        return self.import_tickets(items)

    def _reload_all(self):
        data = self.col.get()
        self._ids = list(data.get("ids", []))
        self._docs = list(data.get("documents", []))
        self._metas = list(data.get("metadatas", []))
        self._rebuild_bm25()

    def _rebuild_bm25(self):
        if not self._docs:
            self._bm25 = None
            return
        corpus = [tokenize(d) for d in self._docs]
        self._bm25 = BM25Okapi(corpus)

    def hybrid_search(
        self, query: str, microservice: Optional[str] = None, top_k: int = 5
    ) -> list[KBMatch]:
        if self.col.count() == 0 or not query.strip():
            return []

        where = {"microservice": microservice} if microservice else None
        if where and self.col.count() == 0:
            where = None

        try:
            q_res = self.col.query(query_texts=[query], n_results=min(top_k * 3, self.col.count()), where=where)
        except Exception:
            where = None
            q_res = self.col.query(query_texts=[query], n_results=min(top_k * 3, self.col.count()))

        vec_scores: dict[str, float] = {}
        ids_q = (q_res.get("ids") or [[]])[0]
        dists = (q_res.get("distances") or [[]])[0]
        metas_q = (q_res.get("metadatas") or [[]])[0]
        docs_q = (q_res.get("documents") or [[]])[0]
        for tid, dist in zip(ids_q, dists):
            vec_scores[tid] = max(0.0, 1.0 - float(dist))

        bm25_scores: dict[str, float] = {}
        if self._bm25:
            scores = self._bm25.get_scores(tokenize(query))
            mx = max(scores) or 1.0
            for i, sc in enumerate(scores):
                bm25_scores[self._ids[i]] = float(sc) / mx

        fused: dict[str, float] = {}
        meta_by_id = {m.get("ticket_id"): m for m in self._metas}
        for tid in set(vec_scores) | set(bm25_scores):
            fused[tid] = 0.6 * vec_scores.get(tid, 0.0) + 0.4 * bm25_scores.get(tid, 0.0)

        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]
        matches: list[KBMatch] = []
        for tid, sc in ranked:
            m = meta_by_id.get(tid, {})
            doc = ""
            for i, d in enumerate(self._docs):
                if self._ids[i] == tid:
                    doc = d
                    break
            matches.append(
                KBMatch(
                    similarity=round(sc, 3),
                    ticket_id=tid,
                    title=m.get("title", ""),
                    root_cause=m.get("root_cause", ""),
                    fix_code=m.get("fix_code", ""),
                    microservice=m.get("microservice", ""),
                    error_code=m.get("error_code", ""),
                )
            )
        return matches

    def list_tickets(self, limit: int = 50, q: Optional[str] = None) -> list[dict]:
        if q and q.strip():
            ms = self.hybrid_search(q, top_k=limit)
            return [
                {
                    "similarity": m.similarity,
                    "ticket_id": m.ticket_id,
                    "title": m.title,
                    "root_cause": m.root_cause,
                    "fix_code": m.fix_code,
                    "microservice": m.microservice,
                    "error_code": m.error_code,
                }
                for m in ms
            ]
        n = len(self._metas)
        out: list[dict] = []
        for i in range(max(0, n - limit), n):
            m = self._metas[i]
            out.append({
                "ticket_id": self._ids[i],
                "title": m.get("title", ""),
                "root_cause": m.get("root_cause", ""),
                "fix_code": m.get("fix_code", ""),
                "microservice": m.get("microservice", ""),
                "error_code": m.get("error_code", ""),
            })
        return out[::-1]

    def delete_tickets(self, ids: list[str]) -> int:
        if not ids:
            return 0
        self.col.delete(ids=ids)
        self._reload_all()
        return len(ids)
