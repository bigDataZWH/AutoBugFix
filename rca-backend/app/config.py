from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# 运行模式
# ---------------------------------------------------------------------------
RuntimeMode = Literal["online_full", "offline_light", "mock_demo"]


@dataclass
class LLMConfig:
    extract_model: str = os.environ.get("EXTRACT_LLM_MODEL", "qwen2.5-coder")
    query_model: str = os.environ.get("QUERY_LLM_MODEL", "deepseek-v3")
    base_url: str = os.environ.get("LLM_BASE_URL", "https://api.opencode.com/v1")
    api_key: str = os.environ.get("LLM_API_KEY", "")
    auth_type: str = os.environ.get("LLM_AUTH_TYPE", "ak_sk")
    fallback_enabled: bool = os.environ.get("LLM_FALLBACK_ENABLED", "true").lower() == "true"
    fallback_model: str = os.environ.get("LLM_FALLBACK_MODEL", "qwen2.5-coder-7b")
    max_retry: int = int(os.environ.get("LLM_MAX_RETRY", "3"))
    timeout_ms: int = int(os.environ.get("LLM_TIMEOUT_MS", "30000"))


@dataclass
class EmbedConfig:
    model: str = os.environ.get("EMBED_MODEL", "bge-m3")
    dim: int = int(os.environ.get("EMBED_DIM", "1024"))
    api_key: str = os.environ.get("EMBED_API_KEY", "")


@dataclass
class Code2CNConfig:
    cache_enabled: bool = os.environ.get("CODE2CN_CACHE_ENABLED", "true").lower() == "true"
    hierarchical_threshold: int = int(os.environ.get("CODE2CN_HIERARCHICAL_THRESHOLD", "5000"))
    max_fn_lines: int = int(os.environ.get("CODE2CN_MAX_FN_LINES", "200"))
    summary_max_chars: int = int(os.environ.get("CODE2CN_SUMMARY_MAX_CHARS", "512"))


@dataclass
class ScoreWeights:
    w1: float = float(os.environ.get("SCORE_W1", "0.3"))
    w2: float = float(os.environ.get("SCORE_W2", "0.3"))
    w3: float = float(os.environ.get("SCORE_W3", "0.2"))
    w4: float = float(os.environ.get("SCORE_W4", "0.2"))

    @classmethod
    def hil_default(cls) -> ScoreWeights:
        return ScoreWeights(w1=0.35, w2=0.30, w3=0.20, w4=0.15)

    def normalize(self) -> None:
        total = self.w1 + self.w2 + self.w3 + self.w4
        if total > 0:
            self.w1 /= total
            self.w2 /= total
            self.w3 /= total
            self.w4 /= total


@dataclass
class GateConfig:
    confidence_threshold: float = float(os.environ.get("GATE_CONFIDENCE_THRESHOLD", "0.6"))
    hil_confidence_threshold: float = float(os.environ.get("HIL_CONFIDENCE_THRESHOLD", "0.7"))
    max_rewrite_rounds: int = int(os.environ.get("GATE_MAX_REWRITE_ROUNDS", "3"))
    max_supplement_rounds: int = int(os.environ.get("GATE_MAX_SUPPLEMENT_ROUNDS", "2"))
    dedup_cosine_threshold: float = float(os.environ.get("GATE_DEDUP_COSINE_THRESHOLD", "0.95"))
    check_call_path_complete: bool = os.environ.get("GATE_CHECK_CALL_PATH", "true").lower() == "true"
    check_root_cause_explainable: bool = os.environ.get("GATE_CHECK_RC_EXPLAINABLE", "true").lower() == "true"
    sse_channel: str = os.environ.get("GATE_SSE_CHANNEL", "hil_panel")
    hang_status: str = os.environ.get("GATE_HANG_STATUS", "HANG")
    retry_queue_max: int = int(os.environ.get("GATE_RETRY_QUEUE_MAX", "3"))


@dataclass
class RedisConfig:
    host: str = os.environ.get("REDIS_HOST", "localhost")
    port: int = int(os.environ.get("REDIS_PORT", "6379"))
    db: int = int(os.environ.get("REDIS_DB", "0"))
    password: str = os.environ.get("REDIS_PASSWORD", "")

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


@dataclass
class CeleryConfig:
    broker_url: str = os.environ.get("CELERY_BROKER_URL", "")
    result_backend: str = os.environ.get("CELERY_RESULT_BACKEND", "")
    task_serializer: str = "json"
    result_serializer: str = "json"
    accept_content: list[str] = field(default_factory=lambda: ["json"])
    task_track_started: bool = True
    task_acks_late: bool = True
    worker_prefetch_multiplier: int = 1


@dataclass
class PostgresConfig:
    host: str = os.environ.get("PG_HOST", "localhost")
    port: int = int(os.environ.get("PG_PORT", "5432"))
    db: str = os.environ.get("PG_DB", "rca")
    user: str = os.environ.get("PG_USER", "rca")
    password: str = os.environ.get("PG_PASSWORD", "rca")

    @property
    def url(self) -> str:
        return f"postgresql+psycopg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


@dataclass
class LightRAGConfig:
    working_dir: str = os.environ.get("LIGHTRAG_WORKING_DIR", "data/lightrag")
    kv_storage: str = os.environ.get("LIGHTRAG_KV_STORAGE", "pgvector")
    graph_storage: str = os.environ.get("LIGHTRAG_GRAPH_STORAGE", "pgvector")
    vector_storage: str = os.environ.get("LIGHTRAG_VECTOR_STORAGE", "pgvector")
    enable_rerank: bool = os.environ.get("LIGHTRAG_ENABLE_RERANK", "true").lower() == "true"
    top_k: int = int(os.environ.get("LIGHTRAG_TOP_K", "10"))


@dataclass
class CodeGraphConfig:
    db_path: str = os.environ.get("CODEGRAPH_DB", ".codegraph/graph.db")
    cg_init: bool = os.environ.get("CODEGRAPH_INIT", "false").lower() == "true"
    cg_index: bool = os.environ.get("CODEGRAPH_INDEX", "false").lower() == "true"


@dataclass
class ServerConfig:
    host: str = os.environ.get("RCA_HOST", "0.0.0.0")
    port: int = int(os.environ.get("RCA_PORT", "8000"))
    debug: bool = os.environ.get("RCA_DEBUG", "false").lower() == "true"
    data_dir: Path = Path(os.environ.get("RCA_DATA_DIR", "data"))
    repos_dir: Path = data_dir / "repos"


@dataclass
class AppConfig:
    runtime_mode: RuntimeMode = os.environ.get("RCA_RUNTIME_MODE", "mock_demo")  # type: ignore[assignment]
    opencode_binary: str = os.environ.get("OPENCODE_BINARY", "opencode")
    opencode_model: Optional[str] = os.environ.get("OPENCODE_MODEL", None)

    llm: LLMConfig = field(default_factory=LLMConfig)
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    code2cn: Code2CNConfig = field(default_factory=Code2CNConfig)
    score_weights: ScoreWeights = field(default_factory=ScoreWeights)
    gate: GateConfig = field(default_factory=GateConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    celery: CeleryConfig = field(default_factory=CeleryConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    lightrag: LightRAGConfig = field(default_factory=LightRAGConfig)
    codegraph: CodeGraphConfig = field(default_factory=CodeGraphConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    def __post_init__(self) -> None:
        if not self.celery.broker_url:
            self.celery.broker_url = self.redis.url
        if not self.celery.result_backend:
            self.celery.result_backend = self.redis.url


config = AppConfig()