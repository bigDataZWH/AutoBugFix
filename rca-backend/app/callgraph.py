from __future__ import annotations
import os
from typing import Optional

import networkx as nx

from .models import CallStackNode


class CallGraph:
    def __init__(self):
        self.g = nx.DiGraph()

    def build_from_opencode(self, oc: dict):
        self.g.clear()
        for sym in oc.get("symbols", []):
            sid = sym.get("id")
            if not sid:
                continue
            self.g.add_node(
                sid,
                type=sym.get("type", "method"),
                file=sym.get("file", ""),
                line=sym.get("line", 0),
                signature=sym.get("signature", ""),
                cls=sym.get("class", ""),
                hotspot_score=0.0,
                hotspot_reason="",
            )
        for e in oc.get("hotspots", []):
            sid = e.get("symbol")
            if sid in self.g:
                self.g.nodes[sid]["hotspot_score"] = float(e.get("score", 0.0))
                self.g.nodes[sid]["hotspot_reason"] = e.get("reason", "")
        for e in oc.get("call_edges", []):
            src, dst = e.get("src"), e.get("dst")
            if src and dst and src in self.g and dst in self.g:
                self.g.add_edge(src, dst, kind=e.get("kind", "call"), file=e.get("file", ""), line=e.get("line", 0))
        for df in oc.get("data_flows", []):
            d = df.get("def")
            for u in df.get("uses", []):
                if d and u and d in self.g and u in self.g:
                    self.g.add_edge(d, u, kind="DATA_FLOWS_TO", var=df.get("var", ""), taint=df.get("taint", ""))

    def node_count(self) -> int:
        return self.g.number_of_nodes()

    def edge_count(self) -> int:
        return self.g.number_of_edges()

    def get_hotspots(self) -> list[CallStackNode]:
        nodes = [(n, d) for n, d in self.g.nodes(data=True) if d.get("hotspot_score", 0) > 0]
        nodes.sort(key=lambda x: x[1].get("hotspot_score", 0), reverse=True)
        return [
            CallStackNode(
                symbol=n,
                file=d.get("file", ""),
                line=d.get("line", 0),
                score=round(d.get("hotspot_score", 0.0), 3),
                reason=d.get("hotspot_reason", ""),
            )
            for n, d in nodes
        ]

    def trace_call_chain(self, start: Optional[str], depth: int = 6) -> list[CallStackNode]:
        if not start or start not in self.g:
            hot = self.get_hotspots()
            return hot[:depth]
        visited: set[str] = set()
        chain: list[CallStackNode] = []
        stack = [(start, 0)]
        while stack and len(chain) < depth:
            node, lvl = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            d = self.g.nodes[node]
            chain.append(
                CallStackNode(
                    symbol=node,
                    file=d.get("file", ""),
                    line=d.get("line", 0),
                    score=round(d.get("hotspot_score", 0.0), 3),
                    reason=d.get("hotspot_reason", ""),
                )
            )
            for nb in self.g.successors(node):
                if nb not in visited:
                    stack.append((nb, lvl + 1))
        chain.sort(key=lambda x: x.score, reverse=True)
        return chain

    def taint_to_hotspot(self, taint: str = "user_input") -> Optional[CallStackNode]:
        for u, v, ed in self.g.edges(data=True):
            if ed.get("kind") == "DATA_FLOWS_TO" and ed.get("taint") == taint:
                if self.g.nodes[v].get("hotspot_score", 0) > 0:
                    d = self.g.nodes[v]
                    return CallStackNode(
                        symbol=v,
                        file=d.get("file", ""),
                        line=d.get("line", 0),
                        score=round(d.get("hotspot_score", 0.0), 3),
                        reason=d.get("hotspot_reason", ""),
                    )
        return None

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        nx.write_graphml(self.g, path)

    def load(self, path: str) -> bool:
        if os.path.exists(path):
            self.g = nx.read_graphml(path)
            return True
        return False
