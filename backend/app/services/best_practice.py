from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from app.config import Settings
from app.models import BestPractice
from app.services.llm import LLMClient, LLMError

logger = logging.getLogger(__name__)


class BestPracticeExplorer:
    """业界最佳实践探索: 联网搜索(ddgs/tavily) + LLM 综合, 失败降级为 LLM-only。"""

    TAVILY_URL = "https://api.tavily.com/search"

    def __init__(self, settings: Settings, llm: LLMClient):
        self.settings = settings
        self.llm = llm

    def explore(
        self, root_cause_summary: str, keywords: Optional[List[str]] = None
    ) -> List[BestPractice]:
        """根据根因探索业界最佳实践, 返回 3-5 条。"""
        summary = (root_cause_summary or "").strip()
        if not summary:
            return []

        search_snippets = self._web_search(summary, keywords)
        try:
            return self._llm_synthesize(summary, search_snippets)
        except Exception as e:
            logger.warning("最佳实践 LLM 综合失败, 返回空: %s", e)
            return []

    # ============ 联网搜索 ============
    def _web_search(self, summary: str, keywords: Optional[List[str]]) -> str:
        """根据 provider 进行联网搜索, 返回拼接摘要文本; 失败返回空串(降级 LLM-only)。"""
        provider = (self.settings.web_search_provider or "none").lower()
        if provider == "none":
            return ""
        query = self._build_query(summary, keywords)
        try:
            if provider == "ddgs":
                return self._search_ddgs(query)
            if provider == "tavily":
                return self._search_tavily(query)
        except Exception as e:
            logger.warning("联网搜索(%s)失败, 降级为 LLM-only: %s", provider, e)
        return ""

    @staticmethod
    def _build_query(summary: str, keywords: Optional[List[str]]) -> str:
        parts = [summary]
        if keywords:
            parts.extend(keywords)
        query = " ".join(parts).strip()
        # 控制查询长度
        return query[:200] if query else summary

    def _search_ddgs(self, query: str) -> str:
        try:
            from ddgs import DDGS  # type: ignore
        except ImportError as e:
            logger.info("ddgs 未安装, 跳过: %s", e)
            return ""
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return ""
        snippets = []
        for r in results:
            title = r.get("title") or ""
            body = r.get("body") or r.get("snippet") or ""
            href = r.get("href") or r.get("link") or ""
            snippets.append(f"- {title}: {body} ({href})")
        return "\n".join(snippets)

    def _search_tavily(self, query: str) -> str:
        api_key = self.settings.tavily_api_key
        if not api_key:
            logger.info("tavily_api_key 未配置, 跳过")
            return ""
        payload = {"api_key": api_key, "query": query, "max_results": 3}
        try:
            resp = httpx.post(self.TAVILY_URL, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("tavily 请求失败: %s", e)
            return ""
        results = data.get("results") or []
        snippets = []
        for r in results:
            title = r.get("title") or ""
            content = r.get("content") or ""
            url = r.get("url") or ""
            snippets.append(f"- {title}: {content} ({url})")
        return "\n".join(snippets)

    # ============ LLM 综合 ============
    def _llm_synthesize(self, summary: str, search_snippets: str) -> List[BestPractice]:
        if not self.llm.configured():
            raise LLMError("LLM 未配置, 无法生成最佳实践")
        system = (
            "你是一位资深技术专家与工程实践顾问。请基于问题根因(可选辅以联网检索结果),"
            "给出 3-5 条可落地的业界最佳实践建议。"
        )
        web_part = f"\n\n## 联网检索参考\n{search_snippets}" if search_snippets else ""
        user = (
            f"## 问题根因\n{summary}{web_part}\n\n"
            "请输出 JSON 数组, 每个元素结构如下:\n"
            '[{"title":"实践标题","description":"具体描述",'
            '"source":"来源(如 联网检索/行业经验)","applicability":"适用场景"}]\n'
            "请确保输出为合法 JSON。"
        )
        data = self.llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return self._parse_practices(data)

    @staticmethod
    def _parse_practices(data) -> List[BestPractice]:
        items = data if isinstance(data, list) else data.get("best_practices") if isinstance(data, dict) else None
        if items is None and isinstance(data, dict):
            # 兼容其它可能的键名
            for k in ("practices", "results", "data"):
                if isinstance(data.get(k), list):
                    items = data[k]
                    break
        if not items or not isinstance(items, list):
            return []
        practices: List[BestPractice] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title", "")).strip()
            if not title:
                continue
            practices.append(
                BestPractice(
                    title=title,
                    description=str(it.get("description", "")) or "",
                    source=it.get("source"),
                    applicability=it.get("applicability"),
                )
            )
        return practices
