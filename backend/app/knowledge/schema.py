from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class KnowledgeRecord(BaseModel):
    """知识库规范记录: 一个历史问题单(根因/验证/代码)。"""

    id: str
    title: str
    summary: Optional[str] = None
    root_cause: str
    verification: Optional[str] = None
    code_snippet: Optional[str] = None
    code_path: Optional[str] = None
    language: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    severity: Optional[str] = None
    product: Optional[str] = None
    component: Optional[str] = None
    source_url: Optional[str] = None
    created_at: Optional[str] = None
    raw: Optional[str] = None

    def to_embed_text(self) -> str:
        """用于向量化与检索的拼接文本。"""
        parts = [
            self.title,
            self.summary or "",
            self.root_cause or "",
            self.verification or "",
            self.code_snippet or "",
            " ".join(self.tags),
        ]
        return "\n".join(p for p in parts if p)


class KnowledgeRecordIn(BaseModel):
    """入库用的记录(无需 id, 自动生成)。"""

    title: str
    summary: Optional[str] = None
    root_cause: str
    verification: Optional[str] = None
    code_snippet: Optional[str] = None
    code_path: Optional[str] = None
    language: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    severity: Optional[str] = None
    product: Optional[str] = None
    component: Optional[str] = None
    source_url: Optional[str] = None
    raw: Optional[str] = None
