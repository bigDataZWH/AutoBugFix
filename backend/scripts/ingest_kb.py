"""知识库批量入库脚本。

用法:
  python scripts/ingest_kb.py data/samples/knowledge_base.json
  python scripts/ingest_kb.py data/my_tickets.csv
  python scripts/ingest_kb.py data/tickets_dir/        # 目录下所有 .json

支持格式:
  - JSON: 数组 或 {"records": [...]} ，字段见 KnowledgeRecordIn
  - CSV:  首行为表头，字段 title/root_cause/verification/code_snippet/...
          tags 字段可用分号或逗号分隔

字段别名兼容:
  root_cause | rootcause | rootCause
  code_snippet | code | snippet
  code_path    | path
  language     | lang
  source_url   | url
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from app.config import settings
from app.knowledge.schema import KnowledgeRecordIn
from app.knowledge.store import KnowledgeStore
from app.services.llm import EmbeddingClient


def parse_tags(val):
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [p.strip() for p in val.replace(";", ",").split(",") if p.strip()]
    return [str(val)]


def extract_records(payload) -> list[KnowledgeRecordIn]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("records") or payload.get("data") or []
        if isinstance(items, dict):
            items = [items]
    else:
        items = []

    records: list[KnowledgeRecordIn] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        root_cause = (it.get("root_cause") or it.get("rootcause") or it.get("rootCause") or "").strip()
        if not title and not root_cause:
            continue
        records.append(
            KnowledgeRecordIn(
                title=title or root_cause,
                summary=it.get("summary"),
                root_cause=root_cause or title,
                verification=it.get("verification"),
                code_snippet=it.get("code_snippet") or it.get("code") or it.get("snippet"),
                code_path=it.get("code_path") or it.get("path"),
                language=it.get("language") or it.get("lang"),
                tags=parse_tags(it.get("tags")),
                severity=it.get("severity"),
                product=it.get("product"),
                component=it.get("component"),
                source_url=it.get("source_url") or it.get("url"),
                raw=it.get("raw"),
            )
        )
    return records


def load_from_file(path: Path) -> list[KnowledgeRecordIn]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".csv":
        reader = csv.DictReader(text.splitlines())
        items = [{(k.strip() if k else k): v for k, v in row.items()} for row in reader]
        return extract_records(items)
    data = json.loads(text)
    return extract_records(data)


def load_from_dir(dir_path: Path) -> list[KnowledgeRecordIn]:
    records: list[KnowledgeRecordIn] = []
    for p in sorted(dir_path.glob("*.json")):
        records.extend(load_from_file(p))
    for p in sorted(dir_path.glob("*.csv")):
        records.extend(load_from_file(p))
    return records


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.exists():
        print(f"[错误] 路径不存在: {target}")
        sys.exit(1)

    if target.is_dir():
        records = load_from_dir(target)
        print(f"[扫描] 目录 {target} 共发现 {len(records)} 条记录")
    else:
        records = load_from_file(target)
        print(f"[加载] 文件 {target} 共解析 {len(records)} 条记录")

    if not records:
        print("[警告] 未解析到任何有效记录，请检查文件格式")
        sys.exit(0)

    settings.ensure_dirs()
    embed = EmbeddingClient(settings)
    store = KnowledgeStore(settings, embed)

    print(f"[向量] provider={settings.embed_provider} model={settings.embed_model}")
    print(f"[入库] 开始向量化并写入 (chroma={settings.chroma_path} sqlite={settings.sqlite_path}) ...")

    result = store.ingest(records)

    print(f"\n===== 入库结果 =====")
    print(f"  成功入库: {result.ingested}")
    print(f"  跳过(重复): {result.skipped}")
    print(f"  错误数:   {len(result.errors)}")
    if result.errors:
        print(f"  错误详情:")
        for e in result.errors[:20]:
            print(f"    - {e}")
    print(f"\n[统计] 知识库当前总量: {store.count()} 条")


if __name__ == "__main__":
    main()
