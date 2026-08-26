"""The VectorStore boundary.

Three methods, deliberately. This abstraction exists because comparing Qdrant against
pgvector is a deliverable, not because the application needs pluggable storage — so it
is kept at the smallest surface that supports that comparison. Anything a backend can do
better than we can, hybrid fusion above all, stays behind this line rather than being
reimplemented above it.
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

    def upsert(self, clauses: Sequence[Clause]) -> int:
        """Index clauses, creating the collection if needed. Returns points written.

        Idempotent: a clause's point id is its deterministic chunk id, so re-indexing
        overwrites rather than duplicating. Safe to call on every startup.
        """
        ...

    def search(
        self,
        query: str,
        *,
        language: Language,
        limit: int = 5,
        regulation: str | None = None,
    ) -> list[SearchHit]:
        """Hybrid dense + sparse search, fused by the engine, most relevant first."""
        ...

    def reset(self) -> None:
        """Drop the collection and everything in it. Destructive; used by tests and reseeds."""
        ...
