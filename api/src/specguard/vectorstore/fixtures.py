"""Record and replay retrieval results.

Retrieval is deterministic given a fixed corpus and query, and it costs no API money —
but it needs a running Qdrant and a 200 MB embedding model, neither of which belongs in
a unit test. Recording the hits lets the whole pipeline replay offline: no vector store,
no model, no network, and the eval that produces the headline accuracy number becomes
something anyone can reproduce from a clone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from specguard.models.common import Language
from specguard.models.corpus import Clause
from specguard.vectorstore.protocol import SearchHit, VectorStore


def search_key(query: str, language: Language, limit: int, regulation: str | None) -> str:
    """Stable id for one search. Hashed because a query is long and full of punctuation."""
    payload = f"{language.value}|{limit}|{regulation or ''}|{query}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


class MissingSearchFixtureError(KeyError):
    """No recorded result for this search.

    Raised rather than returning nothing: an empty result would look like "the corpus has
    no such clause" and quietly turn into an abstention, hiding the missing fixture.
    """


class RecordingStore:
    """Wraps a real store and records every search it performs."""

    def __init__(self, inner: VectorStore, out_dir: Path) -> None:
        self._inner = inner
        self._out = out_dir
        self.searches = 0

    def upsert(self, clauses: Sequence[Clause]) -> int:
        return self._inner.upsert(clauses)

    def reset(self) -> None:
        self._inner.reset()

    def search(
        self,
        query: str,
        *,
        language: Language,
        limit: int = 5,
        regulation: str | None = None,
    ) -> list[SearchHit]:
        hits = self._inner.search(query, language=language, limit=limit, regulation=regulation)
        self._out.mkdir(parents=True, exist_ok=True)
        path = self._out / f"search__{search_key(query, language, limit, regulation)}.json"
        path.write_text(
            json.dumps(
                {
                    "query": query,
                    "language": language.value,
                    "limit": limit,
                    "regulation": regulation,
                    "hits": [hit.model_dump(mode="json") for hit in hits],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.searches += 1
        return hits


class FixtureStore:
    """Replays recorded searches. Satisfies VectorStore without Qdrant or an encoder."""

    def __init__(self, fixture_dir: Path) -> None:
        self._dir = fixture_dir

    def upsert(self, clauses: Sequence[Clause]) -> int:
        raise NotImplementedError("FixtureStore is read-only; it replays recorded searches")

    def reset(self) -> None:
        raise NotImplementedError("FixtureStore is read-only; it replays recorded searches")

    def has(self, query: str, language: Language, limit: int, regulation: str | None) -> bool:
        return (
            self._dir / f"search__{search_key(query, language, limit, regulation)}.json"
        ).exists()

    def search(
        self,
        query: str,
        *,
        language: Language,
        limit: int = 5,
        regulation: str | None = None,
    ) -> list[SearchHit]:
        path = self._dir / f"search__{search_key(query, language, limit, regulation)}.json"
        if not path.exists():
            raise MissingSearchFixtureError(
                f"no recorded search at {path.name} for {query[:60]!r}; re-record with "
                "evals/record_pipeline.py"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [SearchHit.model_validate(hit) for hit in payload["hits"]]
