"""Record the whole pipeline — retrieval and every model call — against live services.

Run deliberately; it spends money and needs a seeded Qdrant. Everything it captures is
replayed offline afterwards, so the eval and the tests need neither.

    uv run python -m evals.record_pipeline
"""

from __future__ import annotations

import argparse
from pathlib import Path

from qdrant_client import QdrantClient

import evals.pipeline as pipeline
from specguard.config import get_settings
from specguard.embedding.encoder import Encoder
from specguard.llm.factory import FIXTURE_DIR, build_client
from specguard.llm.record import RecordingClient
from specguard.vectorstore.fixtures import RecordingStore
from specguard.vectorstore.qdrant import QdrantVectorStore

SEARCH_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "searches"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="openai")
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between live model calls, to stay inside a tokens-per-minute limit.",
    )
    args = parser.parse_args()

    settings = get_settings().model_copy(
        update={"llm_provider": args.provider, "llm_max_retries": 8}
    )
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
    store = RecordingStore(
        QdrantVectorStore(
            client,
            Encoder(settings.dense_embedding_model, settings.sparse_embedding_model),
            collection=settings.qdrant_collection,
        ),
        SEARCH_FIXTURES,
    )
    # Already-recorded calls replay for free, so a run interrupted by a rate limit or a
    # dropped connection resumes without paying for what it already captured.
    recorder = RecordingClient(build_client(settings), FIXTURE_DIR, min_interval_s=args.interval)

    report = pipeline.run(store, recorder, retrieval_limit=settings.retrieval_top_k)
    print(pipeline.render(report))
    print(
        f"\nsearches recorded {store.searches} | live model calls {recorder.calls} | "
        f"replayed {recorder.replayed} | ${recorder.cost_usd:.4f}"
    )


if __name__ == "__main__":
    main()
