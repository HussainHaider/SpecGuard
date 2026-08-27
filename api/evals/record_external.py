"""Record the pipeline over the externally-labelled specifications.

    uv run python -m evals.record_external

Deliberate and paid: it makes real provider calls. Everything it captures is replayed
offline afterwards, so the eval, the tests and CI need neither a key nor a network.

Separate from `record_pipeline.py` because that one walks the seeded generator's own
fixture set, and these documents are built by `specguard.fixtures.external` from the EU
register instead. Resumable: a call already on disk replays for free, so a run interrupted
by a rate limit does not pay twice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qdrant_client import QdrantClient

from specguard.config import get_settings
from specguard.corpus.seed import load_clauses
from specguard.embedding.encoder import Encoder
from specguard.fixtures.external import MANIFEST, SPEC_DIR
from specguard.fixtures.generate import SpecFixture
from specguard.graph.graph import run_check
from specguard.graph.nodes import Dependencies
from specguard.llm.factory import FIXTURE_DIR, build_client
from specguard.llm.record import RecordingClient
from specguard.vectorstore.fixtures import RecordingStore
from specguard.vectorstore.qdrant import QdrantVectorStore

SEARCH_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "searches"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()

    settings = get_settings().model_copy(
        update={"llm_provider": args.provider, "llm_max_retries": 8, "langsmith_tracing": False}
    )
    entries = [
        SpecFixture.model_validate_json(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line
    ]

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
    store = RecordingStore(
        QdrantVectorStore(
            client,
            Encoder(settings.dense_embedding_model, settings.sparse_embedding_model),
            collection=settings.qdrant_collection,
        ),
        SEARCH_FIXTURES,
    )
    recorder = RecordingClient(build_client(settings), FIXTURE_DIR, min_interval_s=args.interval)

    deps = Dependencies(
        settings=settings,
        client=recorder,
        store=store,
        corpus_chunk_ids={clause.chunk_id for clause in load_clauses(settings.corpus_dir)},
    )

    produced: dict[str, str] = {}
    for entry in entries:
        state = run_check(
            deps,
            {
                "job_id": entry.spec_id,
                "correlation_id": entry.spec_id,
                "pdf_path": str(SPEC_DIR / "generated" / entry.filename),
                "language": entry.language.value,
            },
        )
        report = state["report"]
        health = report.result_for(  # the only rule these documents label
            next(iter(entry.expected_verdicts))
        )
        actual = health.verdict.value if health else "ABSENT"
        expected = next(iter(entry.expected_verdicts.values())).value
        mark = "ok " if actual == expected else "DIFF"
        produced[entry.spec_id] = actual
        print(f"  {mark} {entry.spec_id}  expected {expected:5s} got {actual}")

    print(
        f"\nsearches recorded {store.searches} | live model calls {recorder.calls} | "
        f"replayed {recorder.replayed} | ${recorder.cost_usd:.4f}"
    )
    print(json.dumps(produced))


if __name__ == "__main__":
    main()
