"""Spec1 Task 4.2: 旧节点回填迁移 + Spec1 Task 6.3: git diff 增量中文化。

- migrate_old_nodes: 首次查询识别缺失 cn_summary 字段的旧节点，按需中文化回填
- incremental_localize_from_diff: 基于 git diff 计算受影响子树，仅重建受影响函数及调用链上下游
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .code2cn import Code2CN
from .codegraph import CodeGraph
from .config import config
from .models import AstFunctionNode, CodeOutline


@dataclass
class MigrationResult:
    """迁移结果统计。"""
    total_scanned: int = 0
    missing_cn_summary: int = 0
    migrated: int = 0
    skipped: int = 0
    failed: int = 0
    degraded: int = 0
    failed_symbols: list[str] = field(default_factory=list)


def migrate_old_nodes(
    codegraph: Optional[CodeGraph] = None,
    code2cn: Optional[Code2CN] = None,
    batch_size: int = 100,
    source_loader: Optional[Any] = None,
) -> MigrationResult:
    """旧节点回填迁移：识别缺失 cn_summary 的旧节点并按需中文化。

    - 扫描 CodeGraph.nodes 表中 cn_summary IS NULL 的节点
    - 对每个缺失节点按需触发中文化（不阻塞已有查询）
    - 回写 cn_summary 到 SQLite
    """
    cg = codegraph or CodeGraph()
    cn = code2cn or Code2CN()
    result = MigrationResult()

    try:
        import sqlite3
        conn = sqlite3.connect(str(cg.db_path))
        conn.row_factory = sqlite3.Row
    except Exception:
        result.degraded = result.total_scanned
        return result

    try:
        # 检查表是否存在
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'"
        ).fetchone()
        if table_check is None:
            result.degraded = 1
            return result
        # 查找缺失 cn_summary 的节点
        rows = conn.execute(
            "SELECT id, symbol, file, line FROM nodes WHERE cn_summary IS NULL LIMIT ?",
            (batch_size,),
        ).fetchall()

        result.total_scanned = len(rows)
        result.missing_cn_summary = len(rows)

        for row in rows:
            symbol = row["symbol"]
            file_path = row["file"]
            try:
                # 通过 source_loader 加载源码（可 mock）
                source_code = ""
                if source_loader is not None:
                    source_code = source_loader(symbol, file_path, row["line"])
                else:
                    # 默认尝试从文件读取
                    try:
                        fp = Path(file_path)
                        if fp.exists():
                            lines = fp.read_text(encoding="utf-8").splitlines()
                            start = max(0, row["line"] - 1)
                            end = min(len(lines), start + config.code2cn.max_fn_lines)
                            source_code = "\n".join(lines[start:end])
                    except Exception:
                        pass

                if not source_code:
                    result.skipped += 1
                    continue

                req = type("Req", (), {
                    "symbol": symbol,
                    "file": file_path,
                    "source_code": source_code,
                    "signature": "",
                    "language": "java",
                })()
                outline = cn.generate(req)

                if outline.degraded:
                    result.degraded += 1
                else:
                    # 回写 cn_summary
                    conn.execute(
                        "UPDATE nodes SET cn_summary=? WHERE id=?",
                        (outline.cn_summary, row["id"]),
                    )
                    result.migrated += 1
            except Exception:
                result.failed += 1
                result.failed_symbols.append(symbol)

        conn.commit()
    finally:
        conn.close()

    return result


# ============================================================================
# Spec1 Task 6.3: git diff 增量中文化
# ============================================================================

@dataclass
class IncrementalResult:
    """增量中文化结果。"""
    changed_files: list[str] = field(default_factory=list)
    affected_functions: list[str] = field(default_factory=list)
    relocalized: int = 0
    skipped: int = 0
    failed: int = 0
    token_saved_ratio: float = 0.0


def get_git_diff_files(
    repo_path: str,
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
) -> list[str]:
    """获取 git diff 变更文件列表。"""
    repo_dir = Path(repo_path)
    if not repo_dir.exists():
        return []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref, head_ref],
            cwd=str(repo_dir), check=True, capture_output=True, text=True,
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
        return files
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def get_git_diff_functions(
    repo_path: str,
    changed_files: list[str],
    codegraph: Optional[CodeGraph] = None,
) -> list[str]:
    """基于变更文件集计算依赖符号子树，仅返回受影响函数 symbol 列表。

    通过 CodeGraph 查询变更文件中涉及的函数节点及其调用链上下游。
    """
    cg = codegraph or CodeGraph()
    if not changed_files:
        return []

    affected: list[str] = []
    try:
        import sqlite3
        conn = sqlite3.connect(str(cg.db_path))
        conn.row_factory = sqlite3.Row
    except Exception:
        return []

    try:
        # 查找变更文件中的函数节点
        placeholders = ",".join("?" for _ in changed_files)
        rows = conn.execute(
            f"SELECT symbol FROM nodes WHERE file IN ({placeholders})",
            changed_files,
        ).fetchall()
        direct_functions = [r["symbol"] for r in rows]

        # 扩展调用链上下游（callers + callees，1 跳）
        for symbol in direct_functions:
            if symbol not in affected:
                affected.append(symbol)
            # callers
            callers_resp = cg.callers(symbol, depth=1)
            for c in callers_resp.callers:
                sym = c.get("symbol") if isinstance(c, dict) else None
                if sym and sym not in affected:
                    affected.append(sym)
            # callees
            callees_resp = cg.callees(symbol)
            for c in callees_resp.callees:
                sym = c.get("symbol") if isinstance(c, dict) else None
                if sym and sym not in affected:
                    affected.append(sym)
    finally:
        conn.close()

    return affected


def incremental_localize_from_diff(
    repo_path: str,
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    codegraph: Optional[CodeGraph] = None,
    code2cn: Optional[Code2CN] = None,
    source_loader: Optional[Any] = None,
    total_functions: int = 100,
) -> IncrementalResult:
    """git diff 增量中文化：基于变更文件集计算受影响子树，仅重建受影响函数。

    验证：T_inc / T_full ≤ 30%（仅对受影响子树中文化，而非全仓）
    """
    cg = codegraph or CodeGraph()
    cn = code2cn or Code2CN()

    changed_files = get_git_diff_files(repo_path, base_ref, head_ref)
    affected_functions = get_git_diff_functions(repo_path, changed_files, cg)

    result = IncrementalResult(
        changed_files=changed_files,
        affected_functions=affected_functions,
    )

    if not affected_functions:
        return result

    import sqlite3
    try:
        conn = sqlite3.connect(str(cg.db_path))
        conn.row_factory = sqlite3.Row
    except Exception:
        result.failed = len(affected_functions)
        return result

    try:
        for symbol in affected_functions:
            row = conn.execute(
                "SELECT id, file, line FROM nodes WHERE symbol=?", (symbol,),
            ).fetchone()
            if row is None:
                result.skipped += 1
                continue
            try:
                source_code = ""
                if source_loader is not None:
                    source_code = source_loader(symbol, row["file"], row["line"])
                else:
                    try:
                        fp = Path(row["file"])
                        if fp.exists():
                            lines = fp.read_text(encoding="utf-8").splitlines()
                            start = max(0, row["line"] - 1)
                            end = min(len(lines), start + config.code2cn.max_fn_lines)
                            source_code = "\n".join(lines[start:end])
                    except Exception:
                        pass

                if not source_code:
                    result.skipped += 1
                    continue

                req = type("Req", (), {
                    "symbol": symbol,
                    "file": row["file"],
                    "source_code": source_code,
                    "signature": "",
                    "language": "java",
                })()
                outline = cn.generate(req)
                if not outline.degraded and outline.cn_summary:
                    conn.execute(
                        "UPDATE nodes SET cn_summary=? WHERE id=?",
                        (outline.cn_summary, row["id"]),
                    )
                    result.relocalized += 1
                else:
                    result.failed += 1
            except Exception:
                result.failed += 1

        conn.commit()
    finally:
        conn.close()

    # 计算 token 节省比例：仅重建受影响子树 vs 全仓
    if total_functions > 0:
        result.token_saved_ratio = 1.0 - (len(affected_functions) / total_functions)

    return result
