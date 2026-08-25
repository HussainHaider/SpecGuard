"""The VectorStore boundary.

This abstraction is here because comparing Qdrant against pgvector is a deliverable of
the project, not because the application needs pluggable storage. It stays deliberately
thin: anything a backend cannot express — Qdrant's server-side fusion in particular —
belongs behind this interface rather than reimplemented above it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from specguard.models.common import Language, SpecGuardModel
from specguard.models.corpus import Clause


class SearchHit(SpecGuardModel):
    """One retrieved clause and the score that retrieved it."""

    clause: Clause
    score: float


@runtime_checkable
class VectorStore(Protocol):
    """Storage and hybrid retrieval over clause chunks."""

    def ensure_collection(self) -> None:
        """Create the collection if absent. Must be safe to call on every startup."""
        ...

    def upsert_clauses(self, clauses: Sequence[Clause]) -> int:
        """Index clauses, returning how many points were written."""
        ...

    def search(
        self,
        query: str,
        *,
        language: Language,
        limit: int = 8,
        regulation: str | None = None,
    ) -> list[SearchHit]:
        """Hybrid dense + sparse search, fused, most relevant first."""
        ...

    def count(self) -> int:
        """Number of indexed points."""
        ...
