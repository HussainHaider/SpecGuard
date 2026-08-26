"""Application settings.

Every model string, threshold and connection URL lives here and is read from the
environment. Nothing is inlined at a call site: a threshold buried in a rule is a
threshold nobody can find when a verdict looks wrong.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from specguard.embedding.encoder import DENSE_MODEL, SPARSE_MODEL

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Configuration, mirroring ``.env.example``."""

    # The .env lives at the repository root but the package runs from api/, so both
    # locations are searched. Later entries win, letting a local api/.env override.
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://specguard:specguard@localhost:5432/specguard"

    redis_url: str = "redis://localhost:6379/0"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "eu_food_law"

    llm_provider: str = "fake"
    llm_temperature: float = 0.0
    llm_max_retries: int = 2
    llm_timeout_s: float = 60.0
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"

    dense_embedding_model: str = DENSE_MODEL
    sparse_embedding_model: str = SPARSE_MODEL

    # --- Tracing and evaluation ---------------------------------------------
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "specguard"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_dataset: str = "specguard-golden"

    #: The model that judges tier 2's open-ended output. Pinned here rather than left to
    #: deepeval's default, because a judged number is only comparable against another
    #: number from the same judge — and non-negotiable #6 means it never gates a build.
    judge_model: str = "gpt-4.1"

    corpus_dir: Path = Path("corpus")

    #: Where the synthetic specifications and the pre-computed demo reports live.
    #: Explicit for the same reason as corpus_dir: inside the container the package sits
    #: at /app/src/specguard, so walking up from __file__ lands on / rather than on a
    #: repository root that does not exist there. compose sets both paths outright.
    fixtures_dir: Path = Path("fixtures")

    retrieval_top_k: int = Field(default=5, ge=1)
    retrieval_prefetch_k: int = Field(default=50, ge=1)
    min_retrieval_score: float = Field(default=0.35, ge=0.0)
    min_extraction_confidence: float = Field(default=0.60, ge=0.0, le=1.0)

    #: Serve pre-computed reports from ``fixtures/reports`` instead of running the graph.
    #: Zero model calls, zero embedding calls, no Qdrant and no worker — which is what
    #: makes a public deployment safe to leave running and free to leave up.
    demo_mode: bool = False

    #: Origins the review UI is served from. A list rather than "*": this API takes file
    #: uploads and records reviewer decisions, neither of which belongs behind a wildcard.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    graph_version: str = "graph@v1"
    log_level: str = "INFO"

    @field_validator("corpus_dir", "fixtures_dir")
    @classmethod
    def _anchor_to_repo_root(cls, value: Path) -> Path:
        """Resolve a relative path against the repository root, not the working directory.

        The same setting is read from the repo root, from api/, and from inside a
        container. Anchoring relative paths to the root means one value is correct
        everywhere instead of being correct only where it was written.
        """
        return value if value.is_absolute() else (_REPO_ROOT / value).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once."""
    return Settings()
