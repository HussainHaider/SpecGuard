"""Application settings.

Every model string, threshold and connection URL lives here and is read from the
environment. Nothing is inlined at a call site: a threshold buried in a rule is a
threshold nobody can find when a verdict looks wrong.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from specguard.embedding.encoder import DENSE_MODEL, SPARSE_MODEL


class Settings(BaseSettings):
    """Configuration, mirroring ``.env.example``."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://specguard:specguard@localhost:5432/specguard"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "eu_food_law"

    llm_provider: str = "fake"
    llm_temperature: float = 0.0
    llm_max_retries: int = 2
    llm_timeout_s: float = 60.0
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"

    dense_embedding_model: str = DENSE_MODEL
    sparse_embedding_model: str = SPARSE_MODEL

    corpus_dir: Path = Path("../corpus")

    retrieval_top_k: int = Field(default=8, ge=1)
    retrieval_prefetch_k: int = Field(default=25, ge=1)
    min_retrieval_score: float = Field(default=0.35, ge=0.0)
    min_extraction_confidence: float = Field(default=0.60, ge=0.0, le=1.0)

    graph_version: str = "graph@v1"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once."""
    return Settings()
