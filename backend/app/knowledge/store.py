from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import chromadb

from app.knowledge.schema import KnowledgeRecord, KnowledgeRecordIn
from app.models import IngestResult, KnowledgeStats, MatchedCase
from app.services.llm import EmbeddingClient

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """知识库存储层: ChromaDB 向量检索 + SQLite 元数据双写。"""

    COLLECTION_NAME = "knowledge"

    def __init__(self, settings, embed: Optional[EmbeddingClient] = None):
        self.settings = settings
        self.embed = embed
        # 确保目录存在
        Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
        Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        # chroma 持久化客户端
        self._client = chromadb.PersistentClient(path=settings.chroma_path)
        self._collection = None
        # sqlite 元数据库
        self._conn = sqlite3.connect(settings.sqlite_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_db()

    # ---------- 初始化 ----------
    def _init_db(self) -> None:
        """建表: records。"""
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    summary TEXT,
                    root_cause TEXT,
                    verification TEXT,
                    code_snippet TEXT,
                    code_path TEXT,
                    language TEXT,
                    tags TEXT,
                    severity TEXT,
                    product TEXT,
                    component TEXT,
                    source_url TEXT,
                    created_at TEXT,
                    raw TEXT,
                    updated_at TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_title ON records(title)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at DESC)"
            )
            self._conn.commit()

    def _get_or_create_collection(self):
        """获取或创建 chroma collection(余弦距离)。"""
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )
        return self._collection

    # ---------- 入库 ----------
    def ingest(self, records: List[KnowledgeRecordIn]) -> IngestResult:
        """批量入库: 生成 id, 向量化, 写 chroma + sqlite; 重复 title 跳过。"""
        result = IngestResult()
        if not records:
            return result

        for rin in records:
            try:
                title = (rin.title or "").strip()
                if not title:
                    result.errors.append("存在空标题记录, 已跳过")
                    continue
                # 重复 title 跳过
                with self._lock:
                    exists = self._conn.execute(
                        "SELECT 1 FROM records WHERE title = ? LIMIT 1", (title,)
                    ).fetchone()
                if exists:
                    result.skipped += 1
                    continue

                rid = str(uuid.uuid4())
                now = datetime.utcnow().isoformat()

                # 复用 KnowledgeRecord.to_embed_text() 生成规范向量化文本
                rec = KnowledgeRecord(
                    id=rid,
                    created_at=now,
                    **rin.model_dump(exclude_unset=False),
                )
                embed_text = rec.to_embed_text() or title

                # 向量化(使用传入的 EmbeddingClient, 不走 chroma 默认)
                if self.embed is None:
                    result.errors.append(f"[{title}] 向量客户端未配置, 无法入库")
                    continue
                vecs = self.embed.embed([embed_text])
                if not vecs:
                    result.errors.append(f"[{title}] 向量化失败, 已跳过")
                    continue
                vec = vecs[0]

                # 写 chroma
                collection = self._get_or_create_collection()
                collection.add(
                    ids=[rid],
                    embeddings=[vec],
                    documents=[embed_text[:4000]],
                    metadatas=[{"title": title, "severity": rin.severity or ""}],
                )

                # 写 sqlite
                tags_json = json.dumps(rin.tags or [], ensure_ascii=False)
                with self._lock:
                    self._conn.execute(
                        """
                        INSERT INTO records
                        (id, title, summary, root_cause, verification, code_snippet,
                         code_path, language, tags, severity, product, component,
                         source_url, created_at, raw, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rid, title, rin.summary, rin.root_cause, rin.verification,
                            rin.code_snippet, rin.code_path, rin.language, tags_json,
                            rin.severity, rin.product, rin.component, rin.source_url,
                            now, rin.raw, now,
                        ),
                    )
                    self._conn.commit()
                result.ingested += 1
            except Exception as e:
                logger.warning("入库失败 [%s]: %s", getattr(rin, "title", "?"), e)
                result.errors.append(f"[{getattr(rin, 'title', '?')}] {e}")
        return result

    # ---------- 检索 ----------
    def search(self, query: str, top_k: int = 5) -> List[MatchedCase]:
        """向量检索: embed query -> chroma.query -> 取 sqlite 完整记录 -> MatchedCase。"""
        if not query or self.embed is None:
            return []
        try:
            qvec = self.embed.embed([query])
            if not qvec:
                return []
            collection = self._get_or_create_collection()
            res = collection.query(query_embeddings=[qvec[0]], n_results=top_k)
        except Exception as e:
            logger.warning("知识库检索失败: %s", e)
            return []

        ids = (res.get("ids") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]
        if not ids:
            return []

        cases: List[MatchedCase] = []
        for rid, dist in zip(ids, distances):
            try:
                similarity = max(0.0, 1.0 - float(dist) / 2.0)
            except (TypeError, ValueError):
                similarity = 0.0
            row = self._get_record(rid)
            if not row:
                continue
            cases.append(self._row_to_matched_case(row, similarity))
        return cases

    def _get_record(self, record_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM records WHERE id = ? LIMIT 1", (record_id,)
            ).fetchone()

    @staticmethod
    def _row_to_matched_case(row: sqlite3.Row, similarity: float = 0.0) -> MatchedCase:
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except Exception:
            tags = []
        return MatchedCase(
            id=row["id"],
            title=row["title"],
            root_cause=row["root_cause"],
            verification=row["verification"],
            code_snippet=row["code_snippet"],
            code_path=row["code_path"],
            language=row["language"],
            tags=tags,
            similarity=round(similarity, 4),
            source_url=row["source_url"],
        )

    # ---------- 统计/列表 ----------
    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()
        return int(row["c"]) if row else 0

    def stats(self) -> KnowledgeStats:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c, MAX(updated_at) AS last FROM records"
            ).fetchone()
        total = int(row["c"]) if row else 0
        last = row["last"] if row else None
        return KnowledgeStats(
            total=total,
            last_updated=last,
            embed_provider=self.settings.embed_provider,
        )

    def list_all(self, limit: int = 100, offset: int = 0) -> List[dict]:
        """分页列表(用于前端展示)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM records ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        items: List[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
            except Exception:
                d["tags"] = []
            items.append(d)
        return items

    # ---------- 删除 ----------
    def delete(self, record_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM records WHERE id = ? LIMIT 1", (record_id,)
            ).fetchone()
            if not row:
                return False
            self._conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
            self._conn.commit()
        try:
            collection = self._get_or_create_collection()
            collection.delete(ids=[record_id])
        except Exception as e:
            logger.warning("chroma 删除失败 %s: %s", record_id, e)
        return True

    def clear(self) -> int:
        """清空知识库, 返回删除条数。"""
        n = self.count()
        with self._lock:
            self._conn.execute("DELETE FROM records")
            self._conn.commit()
        try:
            self._client.delete_collection(self.COLLECTION_NAME)
        except Exception as e:
            logger.warning("删除 chroma collection 失败: %s", e)
        self._collection = None
        self._get_or_create_collection()
        return n
