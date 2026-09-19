from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
from typing import Optional

from .mock_data import MOCK_OPENCODE_OUTPUT


class OpenCodeAdapter:
    def __init__(self, binary: str = "opencode", model: Optional[str] = None, timeout: int = 180):
        self.binary = binary
        self.model = model
        self.timeout = timeout
        self.available = shutil.which(binary) is not None

    def run_llm(self, prompt: str, cwd: Optional[str] = None) -> str:
        if not self.available:
            return ""
        cmd = [self.binary, "run"]
        if self.model:
            cmd += ["--model", self.model]
        cmd.append(prompt)
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode != 0:
                return ""
            return r.stdout.strip()
        except Exception:
            return ""

    def analyze_code(self, repo: str, branch: str, workdir: Optional[str] = None) -> dict:
        if not self.available or not workdir:
            result = dict(MOCK_OPENCODE_OUTPUT)
            result["repo"] = repo
            result["branch"] = branch
            result["degraded"] = True
            return result

        prompt = (
            "分析当前仓库的调用结构与数据流，仅输出严格 JSON，字段为："
            "symbols[{id,type,file,line,signature,class}], "
            "call_edges[{src,dst,kind,file,line}], "
            "data_flows[{var,def,uses,taint}], "
            "hotspots[{symbol,line,reason,score}]。"
            "不要输出任何 JSON 以外的文字。"
        )
        out = self.run_llm(prompt, cwd=workdir)
        parsed = self._extract_json(out)
        if parsed and "symbols" in parsed:
            parsed["repo"] = repo
            parsed["branch"] = branch
            parsed["degraded"] = False
            return parsed

        result = dict(MOCK_OPENCODE_OUTPUT)
        result["repo"] = repo
        result["branch"] = branch
        result["degraded"] = True
        return result

    def synthesize_report(self, prompt: str, context: dict) -> str:
        full = prompt + "\n\n【证据上下文】\n" + json.dumps(context, ensure_ascii=False, indent=2)
        out = self.run_llm(full)
        if out:
            parsed = self._extract_json(out)
            if parsed:
                return parsed
            return {"_raw": out}
        return {}

    def fetch_yunjie_tickets(self, ticket_refs: list[str]) -> list[dict]:
        if not self.available or not ticket_refs:
            return []
        results: list[dict] = []
        for ref in ticket_refs:
            ref = (ref or "").strip()
            if not ref:
                continue
            prompt = (
                "你是一个云捷系统数据采集助手。请使用你的工具能力从华为云云捷系统获取问题单："
                f"{ref}\n并聚合其关联的所有 MR（Merge Request）。整理为严格 JSON 对象，字段："
                "ticket_id, title, description, root_cause, fix_code, microservice, module, "
                "error_code, severity。约定：root_cause 取自 MR/commit 描述，fix_code 取自"
                "所有关联 MR 的 diff 拼接，一个问题单输出一个对象。不要输出 JSON 以外的文字。"
            )
            out = self.run_llm(prompt)
            parsed = self._extract_json(out)
            if not parsed:
                continue
            if isinstance(parsed, list):
                for d in parsed:
                    if isinstance(d, dict) and d.get("ticket_id"):
                        results.append(d)
            elif isinstance(parsed, dict) and parsed.get("ticket_id"):
                results.append(parsed)
        return results

    @staticmethod
    def _extract_json(text: str) -> dict:
        if not text:
            return {}
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
