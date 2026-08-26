"""Qdrant against pgvector, on the golden retrieval set.

    docker compose up -d db qdrant
    uv run python -m evals.benchmark_retrieval --seed
    uv run python -m evals.benchmark_retrieval

Needs both services and the fastembed model, so it is run deliberately and its output is
pasted into docs/decisions.md rather than being regenerated in CI.

**This benchmark cannot show a performance win and is not looking for one.** The corpus is
734 clauses. Both stores hold all of it in memory, both answer in single-digit
milliseconds, and at that size the difference between an HNSW index in Postgres and one in
a dedicated vector database is measurement noise. What it can show is a *recall*
difference, and that difference is mostly not about storage at all: Qdrant's lexical half
is a bm25 vector from fastembed, Postgres's is ``ts_rank_cd`` over a ``tsvector``. Two
different retrievers, fused the same way.

Both stores are scored on exactly the same queries and the same relevance labels the tier
1 eval uses, so the number here is comparable to the one in the README.
"""

from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass

import evals.metrics as metrics
from evals.golden import load_retrieval
from specguard.config import get_settings
from specguard.corpus.seed import load_clauses
from specguard.embedding.encoder import Encoder
from specguard.vectorstore.protocol import VectorStore


@dataclass(frozen=True)
class Result:
    """One store's showing on the golden retrieval set."""

    store: str
    queries: int
    recall_at_5: float
    hit_rate_at_5: float
    p50_ms: float
    p95_ms: float


def measure(name: str, store: VectorStore, limit: int) -> Result:
    """Replay every golden query against one store, timing each one."""
    scored: list[metrics.RetrievalScored] = []
    latencies: list[float] = []

    for record in load_retrieval():
        started = time.perf_counter()
        hits = store.search(record.query, language=record.language, limit=limit)
        latencies.append((time.perf_counter() - started) * 1000)
        scored.append(
            metrics.RetrievalScored(golden=record, retrieved=[hit.clause.chunk_id for hit in hits])
        )

    ordered = sorted(latencies)
    return Result(
        store=name,
        queries=len(scored),
        recall_at_5=statistics.fmean(item.recall for item in scored),
        hit_rate_at_5=statistics.fmean(float(item.hit) for item in scored),
        p50_ms=ordered[len(ordered) // 2],
        p95_ms=ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
    )


def render(results: list[Result]) -> str:
    """A markdown table, ready to paste into docs/decisions.md."""
    lines = [
        "| store | queries | recall@5 | hit rate@5 | p50 | p95 |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.store} | {result.queries} | {result.recall_at_5:.1%} | "
            f"{result.hit_rate_at_5:.1%} | {result.p50_ms:.1f} ms | {result.p95_ms:.1f} ms |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed", action="store_true", help="Index the corpus into both stores first."
    )
    args = parser.parse_args()

    import psycopg
    from qdrant_client import QdrantClient

    from specguard.vectorstore.pgvector import PgVectorStore
    from specguard.vectorstore.qdrant import QdrantVectorStore

    settings = get_settings()
    encoder = Encoder(settings.dense_embedding_model, settings.sparse_embedding_model)

    qdrant = QdrantVectorStore(
        QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60),
        encoder,
        collection=settings.qdrant_collection,
    )
    # psycopg wants a libpq URL; the app's is a SQLAlchemy one.
    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as connection:
        postgres = PgVectorStore(connection, encoder)

        if args.seed:
            clauses = load_clauses(settings.corpus_dir)
            print(f"indexing {len(clauses)} clauses into both stores")
            print(f"  qdrant   {qdrant.upsert(clauses)}")
            print(f"  pgvector {postgres.upsert(clauses)}")

        results = [
            measure("qdrant (dense + bm25, server-side RRF)", qdrant, settings.retrieval_top_k),
            measure("pgvector (dense + tsvector, RRF in SQL)", postgres, settings.retrieval_top_k),
        ]

    print()
    print(render(results))
    print()
    print(
        "At 734 clauses a latency difference here is noise: both stores hold the whole "
        "corpus in memory. The recall difference is the finding worth reading, and it is "
        "mostly a difference between bm25 and ts_rank_cd rather than between the stores."
    )


if __name__ == "__main__":
    main()
