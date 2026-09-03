from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

from .config import config
from .mock_data import MOCK_OPENCODE_OUTPUT
from .models import CPGEdge, CPGNode, CallersResponse, CalleesResponse, ExploreResponse, TaintResponse


def _normalize_symbol(symbol: str) -> str:
    if symbol.startswith("sym:"):
        return symbol
    parts = symbol.rsplit(".", 1)
    if len(parts) == 2:
        return f"sym:{parts[0]}:{parts[1]}"
    return symbol


class CodeGraph:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path or config.codegraph.db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id          INTEGER PRIMARY KEY,
                    symbol      TEXT NOT NULL UNIQUE,
                    type        TEXT CHECK(type IN ('function','class','method')),
                    file        TEXT NOT NULL,
                    line        INTEGER NOT NULL,
                    fan_in      INTEGER DEFAULT 0,
                    fan_out     INTEGER DEFAULT 0,
                    complexity  INTEGER DEFAULT 0,
                    cn_summary  TEXT
                );
                CREATE TABLE IF NOT EXISTS edges (
                    id      INTEGER PRIMARY KEY,
                    src_id  INTEGER NOT NULL REFERENCES nodes(id),
                    tgt_id  INTEGER NOT NULL REFERENCES nodes(id),
                    type    TEXT CHECK(type IN ('call','inherit','ref')),
                    weight  REAL DEFAULT 1.0
                );
                CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
                CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges(tgt_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                    symbol, cn_summary, file,
                    content='nodes', content_rowid='id'
                );
            """)

    def seed_mock_data(self) -> int:
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if count > 0:
            return count
        symbols = MOCK_OPENCODE_OUTPUT.get("symbols", [])
        edges = MOCK_OPENCODE_OUTPUT.get("call_edges", [])
        fan_in: dict[str, int] = {}
        fan_out: dict[str, int] = {}
        for e in edges:
            fan_out[e["src"]] = fan_out.get(e["src"], 0) + 1
            fan_in[e["dst"]] = fan_in.get(e["dst"], 0) + 1
        for s in symbols:
            sym = s["id"]
            node = CPGNode(
                symbol=sym,
                type=s.get("type", "method"),
                file=s.get("file", ""),
                line=s.get("line", 0),
                fan_in=fan_in.get(sym, 0),
                fan_out=fan_out.get(sym, 0),
                complexity=max(fan_in.get(sym, 0), 1),
                cn_summary=s.get("cn_summary"),
            )
            self.upsert_node(node)
        for e in edges:
            self.upsert_edge(e["src"], e["dst"], e.get("kind", "call"), 1.0)
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def upsert_node(self, node: CPGNode) -> int:
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO nodes (symbol, type, file, line, fan_in, fan_out, complexity, cn_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    type=excluded.type, file=excluded.file, line=excluded.line,
                    fan_in=excluded.fan_in, fan_out=excluded.fan_out,
                    complexity=excluded.complexity, cn_summary=COALESCE(excluded.cn_summary, nodes.cn_summary)
            """, (node.symbol, node.type, node.file, node.line,
                  node.fan_in, node.fan_out, node.complexity, node.cn_summary))
            node_id = cursor.lastrowid
            conn.execute("INSERT INTO nodes_fts(rowid, symbol, cn_summary, file) VALUES (?, ?, ?, ?)", (
                node_id, node.symbol, node.cn_summary or "", node.file,
            ))
            return node_id

    def upsert_edge(self, src_symbol: str, tgt_symbol: str, edge_type: str = "call", weight: float = 1.0) -> None:
        with self._conn() as conn:
            src = conn.execute("SELECT id FROM nodes WHERE symbol=?", (src_symbol,)).fetchone()
            tgt = conn.execute("SELECT id FROM nodes WHERE symbol=?", (tgt_symbol,)).fetchone()
            if src is None or tgt is None:
                return
            conn.execute("""
                INSERT INTO edges (src_id, tgt_id, type, weight)
                VALUES (?, ?, ?, ?)
                ON CONFLICT DO NOTHING
            """, (src["id"], tgt["id"], edge_type, weight))

    def _get_node_by_symbol(self, symbol: str) -> Optional[dict[str, Any]]:
        normalized = _normalize_symbol(symbol)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE symbol=?", (normalized,)).fetchone()
            if row is None and normalized != symbol:
                row = conn.execute("SELECT * FROM nodes WHERE symbol=?", (symbol,)).fetchone()
            return dict(row) if row else None

    def _build_node(self, row) -> CPGNode:
        data = dict(row) if not isinstance(row, dict) else row
        return CPGNode(
            symbol=data["symbol"], type=data["type"], file=data["file"],
            line=data["line"], fan_in=data["fan_in"], fan_out=data["fan_out"],
            complexity=data["complexity"], cn_summary=data.get("cn_summary"),
        )

    def callers(self, symbol: str, depth: int = 2) -> CallersResponse:
        node = self._get_node_by_symbol(symbol)
        if node is None:
            return CallersResponse(callers=[], edges=[], truncated=False)

        actual_symbol = node["symbol"]
        seen: set[str] = set()
        callers: list[CPGNode] = []
        edges: list[CPGEdge] = []
        current_layer = {actual_symbol}

        for _ in range(depth):
            if not current_layer:
                break
            placeholders = ",".join("?" for _ in current_layer)
            with self._conn() as conn:
                rows = conn.execute(f"""
                    SELECT n.*, e.type as etype, e.weight
                    FROM edges e
                    JOIN nodes n ON n.id = e.src_id
                    JOIN nodes tgt ON tgt.id = e.tgt_id
                    WHERE tgt.symbol IN ({placeholders}) AND e.type='call'
                """, list(current_layer)).fetchall()

            next_layer: set[str] = set()
            for r in rows:
                if r["symbol"] not in seen:
                    seen.add(r["symbol"])
                    callers.append(self._build_node(r))
                    next_layer.add(r["symbol"])
                edges.append(CPGEdge(src=r["symbol"], tgt=actual_symbol, type="call", weight=r["weight"]))
            current_layer = next_layer

        return CallersResponse(
            callers=[c.model_dump() for c in callers],
            edges=[e.model_dump() for e in edges],
            truncated=False,
        )

    def callees(self, symbol: str) -> CalleesResponse:
        node = self._get_node_by_symbol(symbol)
        if node is None:
            return CalleesResponse(callees=[], edges=[])

        actual_symbol = node["symbol"]
        callees: list[CPGNode] = []
        edges: list[CPGEdge] = []
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT n.*, e.type as etype, e.weight
                FROM edges e
                JOIN nodes n ON n.id = e.tgt_id
                WHERE e.src_id = ?
            """, (node["id"],)).fetchall()

        for r in rows:
            callees.append(self._build_node(r))
            edges.append(CPGEdge(src=actual_symbol, tgt=r["symbol"], type=r["etype"], weight=r["weight"]))

        return CalleesResponse(
            callees=[c.model_dump() for c in callees],
            edges=[e.model_dump() for e in edges],
        )

    def explore(self, symbol: str, hops: int = 2) -> ExploreResponse:
        node = self._get_node_by_symbol(symbol)
        if node is None:
            return ExploreResponse(nodes=[], edges=[], center=symbol)

        actual_symbol = node["symbol"]
        seen: set[str] = {actual_symbol}
        nodes_map: dict[str, CPGNode] = {actual_symbol: self._build_node(node)}
        edges: list[CPGEdge] = []
        current_layer = {actual_symbol}

        for _ in range(hops):
            if not current_layer:
                break
            placeholders = ",".join("?" for _ in current_layer)
            params = list(current_layer) * 2
            with self._conn() as conn:
                rows = conn.execute(f"""
                    SELECT n.*, e.type as etype, e.weight, e.src_id as esrc, e.tgt_id as etgt
                    FROM edges e
                    JOIN nodes n ON n.id IN (e.src_id, e.tgt_id)
                    WHERE (e.src_id IN (SELECT id FROM nodes WHERE symbol IN ({placeholders}))
                        OR e.tgt_id IN (SELECT id FROM nodes WHERE symbol IN ({placeholders})))
                """, params).fetchall()

            next_layer: set[str] = set()
            for r in rows:
                for sym_col in ["symbol"]:
                    sym = r[sym_col]
                    if sym not in seen:
                        seen.add(sym)
                        nodes_map[sym] = self._build_node(r)
                        next_layer.add(sym)

                src_sym = conn.execute("SELECT symbol FROM nodes WHERE id=?", (r["esrc"],)).fetchone()
                tgt_sym = conn.execute("SELECT symbol FROM nodes WHERE id=?", (r["etgt"],)).fetchone()
                if src_sym and tgt_sym:
                    edges.append(CPGEdge(src=src_sym["symbol"], tgt=tgt_sym["symbol"], type=r["etype"], weight=r["weight"]))

            current_layer = next_layer

        return ExploreResponse(
            nodes=[n.model_dump() for n in nodes_map.values()],
            edges=[e.model_dump() for e in edges],
            center=actual_symbol,
        )

    def taint(self, entry: str, sink: str) -> TaintResponse:
        entry_node = self._get_node_by_symbol(entry)
        sink_node = self._get_node_by_symbol(sink)
        if entry_node is None or sink_node is None:
            return TaintResponse(
                paths=[], entry_found=entry_node is not None, sink_found=sink_node is not None
            )

        paths: list[dict[str, Any]] = []
        visited: set[int] = set()

        def dfs(current_id: int, path: list[dict[str, Any]]) -> None:
            if current_id in visited:
                return
            visited.add(current_id)
            with self._conn() as conn:
                row = conn.execute("SELECT * FROM nodes WHERE id=?", (current_id,)).fetchone()
                if row:
                    path.append({
                        "symbol": row["symbol"], "file": row["file"],
                        "line": row["line"], "type": "call",
                    })
            if current_id == sink_node["id"]:
                paths.append({"hops": list(path), "reachable": True})
                path.pop()
                visited.discard(current_id)
                return
            with self._conn() as conn:
                callees = conn.execute(
                    "SELECT tgt_id FROM edges WHERE src_id=? AND type='call'", (current_id,)
                ).fetchall()
            for callee in callees:
                dfs(callee["tgt_id"], path)
            path.pop()
            visited.discard(current_id)

        dfs(entry_node["id"], [])
        return TaintResponse(
            paths=paths,
            entry_found=True,
            sink_found=True,
        )

    def search(self, query: str, limit: int = 10) -> list[CPGNode]:
        if not query or not query.strip():
            return []
        with self._conn() as conn:
            try:
                rows = conn.execute("""
                    SELECT n.* FROM nodes_fts f
                    JOIN nodes n ON n.id = f.rowid
                    WHERE nodes_fts MATCH ?
                    LIMIT ?
                """, (query.strip(), limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like_pat = f"%{query.strip()}%"
                rows = conn.execute("""
                    SELECT * FROM nodes
                    WHERE symbol LIKE ? OR cn_summary LIKE ? OR file LIKE ?
                    LIMIT ?
                """, (like_pat, like_pat, like_pat, limit)).fetchall()
        return [self._build_node(dict(r)) for r in rows]

    def build(self, repo_path: str, branch: str = "main") -> None:
        repo_dir = Path(repo_path)
        if not repo_dir.exists():
            raise FileNotFoundError(f"Repository not found: {repo_path}")
        try:
            subprocess.run(
                ["codegraph", "init", "-i"],
                cwd=str(repo_dir), check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["codegraph", "index"],
                cwd=str(repo_dir), check=True, capture_output=True, text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.init_schema()

    def incremental_update(self, repo_path: str, changed_files: list[str]) -> None:
        repo_dir = Path(repo_path)
        if not repo_dir.exists():
            return
        try:
            subprocess.run(
                ["codegraph", "index", "--incremental", *changed_files],
                cwd=str(repo_dir), check=True, capture_output=True, text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass


codegraph = CodeGraph()