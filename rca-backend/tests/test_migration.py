"""Spec1 Task 4.2/6.3: 旧节点回填迁移 + git diff 增量中文化 UT 测试套件。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.migration import (
    MigrationResult, IncrementalResult,
    migrate_old_nodes, get_git_diff_files, get_git_diff_functions,
    incremental_localize_from_diff,
)
from app.codegraph import CodeGraph


@pytest.fixture
def tmp_codegraph(tmp_path):
    """临时 CodeGraph SQLite 库。"""
    db_path = tmp_path / "graph.db"
    cg = CodeGraph(db_path=str(db_path))
    cg.init_schema()
    # 插入测试节点
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO nodes (symbol, type, file, line, cn_summary) VALUES (?, ?, ?, ?, ?)",
            [
                ("OrderService.create", "method", "OrderService.java", 50, None),
                ("OrderService.submit", "method", "OrderService.java", 80, "已存在中文"),
                ("StockService.deduct", "method", "StockService.java", 100, None),
            ],
        )
        # 插入调用边
        conn.execute("INSERT INTO edges (src_id, tgt_id, type, weight) VALUES (1, 3, 'call', 1.0)")
        conn.commit()
    return cg, db_path


class TestMigrationResult:
    """UT 1: MigrationResult 数据结构"""

    def test_defaults(self):
        r = MigrationResult()
        assert r.total_scanned == 0
        assert r.migrated == 0
        assert r.failed_symbols == []


class TestMigrateOldNodes:
    """UT 2: 旧节点回填迁移"""

    def test_finds_missing_cn_summary_nodes(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        # mock code2cn 不实际调用 LLM
        mock_cn = MagicMock()
        from app.models import CodeOutline
        mock_cn.generate.return_value = CodeOutline(
            symbol="OrderService.create", file="OrderService.java",
            cn_summary="创建订单逻辑", external_calls=[], failure_paths=[],
        )
        # 提供源码加载器
        def loader(symbol, file, line):
            return f"// source of {symbol}"
        result = migrate_old_nodes(cg, mock_cn, batch_size=100, source_loader=loader)
        # 应识别出 2 个缺失 cn_summary 的节点
        assert result.missing_cn_summary == 2
        assert result.migrated >= 1

    def test_skips_when_no_source(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        mock_cn = MagicMock()
        # source_loader 返回空 → skipped
        result = migrate_old_nodes(cg, mock_cn, source_loader=lambda *a: "")
        assert result.skipped > 0

    def test_handles_db_error(self, tmp_path):
        """DB 表不存在时降级返回（CodeGraph 会创建目录，但表未初始化）"""
        db_path = tmp_path / "empty.db"
        # 不调用 init_schema，直接构造 CodeGraph
        cg = CodeGraph(db_path=str(db_path))
        mock_cn = MagicMock()
        result = migrate_old_nodes(cg, mock_cn)
        # 表不存在，不抛异常，返回降级结果
        assert isinstance(result, MigrationResult)
        assert result.degraded >= 1


class TestGetGitDiff:
    """UT 3: git diff 变更文件获取"""

    def test_no_repo_returns_empty(self):
        files = get_git_diff_files("/nonexistent/repo")
        assert files == []

    def test_mock_subprocess(self, tmp_path):
        with patch("app.migration.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="file1.java\nfile2.py\n")
            files = get_git_diff_files(str(tmp_path))
            assert "file1.java" in files
            assert "file2.py" in files


class TestGetGitDiffFunctions:
    """UT 4: 变更文件 → 受影响函数子树"""

    def test_finds_functions_in_changed_files(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        funcs = get_git_diff_functions("/fake/repo", ["OrderService.java"], cg)
        # OrderService.create 和 OrderService.submit 都在 OrderService.java
        assert "OrderService.create" in funcs
        assert "OrderService.submit" in funcs

    def test_expands_callers_callees(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        # OrderService.create (id=1) 有 edge 到 StockService.deduct (id=3)
        funcs = get_git_diff_functions("/fake/repo", ["OrderService.java"], cg)
        # 应扩展 callees
        assert "StockService.deduct" in funcs

    def test_empty_changed_files(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        funcs = get_git_diff_functions("/fake/repo", [], cg)
        assert funcs == []


class TestIncrementalLocalize:
    """UT 5: git diff 增量中文化"""

    def test_relocalizes_affected(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        mock_cn = MagicMock()
        from app.models import CodeOutline
        mock_cn.generate.return_value = CodeOutline(
            symbol="OrderService.create", file="OrderService.java",
            cn_summary="增量更新", external_calls=[], failure_paths=[],
        )
        # 提供源码加载器
        def loader(symbol, file, line):
            return f"// source of {symbol}"
        # mock git diff 返回变更文件
        with patch("app.migration.get_git_diff_files", return_value=["OrderService.java"]):
            result = incremental_localize_from_diff(
                "/fake/repo", codegraph=cg, code2cn=mock_cn, total_functions=100,
                source_loader=loader,
            )
        assert len(result.changed_files) == 1
        assert result.relocalized >= 1

    def test_token_saved_ratio(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        mock_cn = MagicMock()
        from app.models import CodeOutline
        mock_cn.generate.return_value = CodeOutline(
            symbol="OrderService.create", file="OrderService.java",
            cn_summary="增量", external_calls=[], failure_paths=[],
        )
        with patch("app.migration.get_git_diff_files", return_value=["OrderService.java"]):
            result = incremental_localize_from_diff(
                "/fake/repo", codegraph=cg, code2cn=mock_cn, total_functions=100,
            )
        # 增量中文化应节省大部分 token（仅重建受影响子树）
        assert result.token_saved_ratio > 0.0

    def test_no_changes_no_relocalize(self, tmp_codegraph):
        cg, db_path = tmp_codegraph
        with patch("app.migration.get_git_diff_files", return_value=[]):
            result = incremental_localize_from_diff(
                "/fake/repo", codegraph=cg, total_functions=100,
            )
        assert result.relocalized == 0
