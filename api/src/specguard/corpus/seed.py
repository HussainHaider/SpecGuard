"""Seed the Qdrant collection from the plain text under ``corpus/raw/``.

Idempotent by construction: a clause's point id is its deterministic chunk id, so
re-seeding overwrites the same points rather than accumulating duplicates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qdrant_client import QdrantClient

from specguard.config import get_settings
from specguard.corpus.sources import raw_filename
from specguard.embedding.encoder import Encoder
from specguard.models.corpus import Clause, CorpusDocument
from specguard.retrieval.chunking import chunk_document
from specguard.vectorstore.qdrant import QdrantVectorStore


class CorpusNotFetchedError(FileNotFoundError):
    """Raised when ``corpus/raw/`` has not been populated yet."""


def load_documents(corpus_dir: Path) -> list[CorpusDocument]:
    """Read the manifest written by the fetcher."""
    manifest = corpus_dir / "sources.json"
    if not manifest.exists():
        raise CorpusNotFetchedError(
            f"{manifest} not found — run `python -m specguard.corpus.fetch` first"
        )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return [CorpusDocument.model_validate(entry) for entry in payload]


def load_clauses(corpus_dir: Path) -> list[Clause]:
    """Chunk every fetched document into clauses."""
    clauses: list[Clause] = []
    for document in load_documents(corpus_dir):
        path = corpus_dir / "raw" / raw_filename(document.celex, document.language)
        if not path.exists():
            raise CorpusNotFetchedError(f"{path} is missing — re-run the fetcher")
        clauses.extend(
            chunk_document(
                path.read_text(encoding="utf-8"),
                regulation=document.regulation,
                celex=document.celex,
                language=document.language,
                source_version=document.source_version,
            )
        )
    return clauses


def seed(corpus_dir: Path, store: QdrantVectorStore) -> int:
    """Chunk and index the whole corpus, returning the number of points written."""
    clauses = load_clauses(corpus_dir)
    ids = {clause.chunk_id for clause in clauses}
    if len(ids) != len(clauses):
        # Two clauses sharing an id means one would overwrite the other and a stored
        # citation would resolve to the wrong text. Refuse rather than index it.
        raise ValueError(
            f"{len(clauses) - len(ids)} duplicate chunk ids across the corpus; "
            "indexing would silently drop clauses"
        )
    return store.upsert(clauses)


def main() -> None:
    """CLI: ``python -m specguard.corpus.seed``."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=settings.corpus_dir)
    parser.add_argument("--collection", default=settings.qdrant_collection)
    parser.add_argument(
        "--reset", action="store_true", help="Drop the collection first. Destructive."
    )
    args = parser.parse_args()

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    store = QdrantVectorStore(
        client,
        Encoder(settings.dense_embedding_model, settings.sparse_embedding_model),
        collection=args.collection,
    )
    if args.reset:
        # Destructive, so it never happens implicitly: a re-seed overwrites by chunk id
        # anyway, and dropping the collection is only right when the chunking changed.
        print(f"Dropping collection '{args.collection}'")
        store.reset()

    written = seed(args.corpus_dir, store)
    print(f"Indexed {written} clauses into '{args.collection}'")


if __name__ == "__main__":
    main()
