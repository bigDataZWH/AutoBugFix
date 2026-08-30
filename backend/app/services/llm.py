from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    """OpenAI 兼容大模型客户端: 华为内部模型 / 外部 API / 本地 Ollama 统一接入。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key or "EMPTY",
                timeout=self.settings.llm_timeout,
            )
        return self._client

    def configured(self) -> bool:
        return self.settings.llm_configured

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    def chat(
        self,
        messages: List[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        if not self.configured():
            raise LLMError("LLM 未配置: 请在 .env 设置 LLM_BASE_URL / LLM_MODEL")
        kwargs = dict(
            model=self.settings.llm_model,
            messages=messages,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
        )
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:
            raise LLMError(f"LLM 调用失败: {e}") from e

    def chat_json(self, messages: List[dict], **kw) -> dict:
        """调用 LLM 并解析为 JSON, 容忍非严格 json_mode 的文本包裹。"""
        text = self.chat(messages, json_mode=True, **kw)
        return _parse_json(text)

    def health(self) -> tuple:
        try:
            self.client.models.list()
            return True, "LLM 连接正常"
        except Exception as e:
            return False, f"LLM 连接失败: {e}"


def _parse_json(text: str) -> dict:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {"_raw": text}


# ============ Embedding ============
class EmbeddingClient:
    """向量客户端: api(OpenAI 兼容 /embeddings) 或 local(sentence-transformers)。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._api: Optional[OpenAI] = None
        self._local = None

    def _api_client(self) -> OpenAI:
        if self._api is None:
            self._api = OpenAI(
                base_url=self.settings.embed_base_url or self.settings.llm_base_url,
                api_key=self.settings.embed_api_key or self.settings.llm_api_key or "EMPTY",
                timeout=60,
            )
        return self._api

    def _local_model(self):
        if self._local is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise LLMError(
                    "本地向量需要 sentence-transformers, 请: pip install sentence-transformers"
                ) from e
            self._local = SentenceTransformer(self.settings.embed_model)
        return self._local

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if self.settings.embed_provider == "local":
            model = self._local_model()
            vecs = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
            return [v.tolist() for v in vecs]
        # api
        client = self._api_client()
        try:
            resp = client.embeddings.create(model=self.settings.embed_model, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as e:
            raise LLMError(f"向量接口调用失败: {e}") from e

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]

    def health(self) -> tuple:
        try:
            self.embed(["ping"])
            return True, f"向量正常(provider={self.settings.embed_provider})"
        except Exception as e:
            return False, f"向量不可用: {e}"
