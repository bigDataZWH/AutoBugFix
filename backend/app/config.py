from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # 应用
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "*"

    # 大模型 (OpenAI 兼容)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    llm_timeout: float = 180.0
    llm_temperature: float = 0.3

    # 向量
    embed_provider: str = "api"  # api | local
    embed_base_url: Optional[str] = None
    embed_api_key: Optional[str] = None
    embed_model: str = "BAAI/bge-small-zh-v1.5"

    # CodeHub
    codehub_base_url: str = "https://codehub.example.com"
    codehub_token: str = ""
    codehub_api_prefix: str = "/api/v4"
    codehub_mock: bool = True

    # 存储
    data_dir: str = "data"
    chroma_path: str = "data/chroma"
    sqlite_path: str = "data/db.sqlite"
    upload_dir: str = "data/uploads"

    # 联网最佳实践搜索
    web_search_provider: str = "none"  # none | ddgs | tavily
    tavily_api_key: Optional[str] = None

    # ---- 派生 ----
    @property
    def cors_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def codehub_configured(self) -> bool:
        return bool(self.codehub_token and not self.codehub_mock)

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.chroma_path, self.upload_dir):
            Path(p).mkdir(parents=True, exist_ok=True)
        # sqlite 父目录
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()


def get_settings() -> "Settings":
    return settings
