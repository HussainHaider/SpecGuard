"""Qdrant-backed vector store: one collection, two named vectors, server-side fusion."""

from __future__ import annotations

from collections.abc import Sequence

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from specguard.embedding.encoder import Encoder
from specguard.models.common import Language
from specguard.models.corpus import Clause
from specguard.vectorstore.protocol import SearchHit

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"
DEFAULT_COLLECTION = "eu_food_law"

#: Prefetch depth per branch before fusion. Wider than the final limit on purpose:
#: fusion can only reorder what each branch actually returned.
PREFETCH_LIMIT = 25


class QdrantVectorStore:
    """Clause storage and hybrid retrieval in a single Qdrant collection.

    Dense and sparse live as two named vectors on **one point per clause**, written in a
    single upsert. Two collections, or two writes, would let the representations drift:
    a re-index that failed halfway would leave clauses findable lexically but not
    semantically, and nothing in the system would notice.

    Fusion is Qdrant's own RRF via ``query_points`` with ``prefetch``. Doing it in
    application code would mean paging both result sets back over the wire to recompute
    what the engine already computed.
    """

    def __init__(
        self,
        client: QdrantClient,
        encoder: Encoder,
        collection: str = DEFAULT_COLLECTION,
    ) -> None:
        self._client = client
        self._encoder = encoder
        self._collection = collection

    def ensure_collection(self) -> None:
        """Create the collection with both named vectors if it does not exist."""
        if self._client.collection_exists(self._collection):
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                DENSE_VECTOR: qm.VectorParams(
                    size=self._encoder.dimensions, distance=qm.Distance.COSINE
                )
            },
            sparse_vectors_config={SPARSE_VECTOR: qm.SparseVectorParams()},
        )

    def upsert_clauses(self, clauses: Sequence[Clause]) -> int:
        """Index clauses, one point each, both vectors written together."""
        if not clauses:
            return 0
        texts = [clause.embedding_text for clause in clauses]
        dense = self._encoder.encode_passages(texts)
        sparse = self._encoder.encode_sparse(texts)

        points = [
            qm.PointStruct(
                # The chunk id is a UUIDv5, which is a legal Qdrant point id, so a
                # re-index overwrites the same point instead of duplicating it.
                id=clause.chunk_id,
                vector={
                    DENSE_VECTOR: dense_vector,
                    SPARSE_VECTOR: qm.SparseVector(indices=s.indices, values=s.values),
                },
                payload=clause.model_dump(mode="json"),
            )
            for clause, dense_vector, s in zip(clauses, dense, sparse, strict=True)
        ]
        self._client.upsert(collection_name=self._collection, points=points, wait=True)
        return len(points)

    def search(
        self,
        query: str,
        *,
        language: Language,
        limit: int = 8,
        regulation: str | None = None,
    ) -> list[SearchHit]:
        """Hybrid search with server-side reciprocal rank fusion."""
        conditions: list[qm.Condition] = [
            qm.FieldCondition(key="language", match=qm.MatchValue(value=language.value))
        ]
        if regulation is not None:
            conditions.append(
                qm.FieldCondition(key="regulation", match=qm.MatchValue(value=regulation))
            )
        query_filter = qm.Filter(must=conditions)

        sparse = self._encoder.encode_sparse_query(query)
        response = self._client.query_points(
            collection_name=self._collection,
            prefetch=[
                qm.Prefetch(
                    query=self._encoder.encode_query(query),
                    using=DENSE_VECTOR,
                    limit=PREFETCH_LIMIT,
                    filter=query_filter,
                ),
                qm.Prefetch(
                    query=qm.SparseVector(indices=sparse.indices, values=sparse.values),
                    using=SPARSE_VECTOR,
                    limit=PREFETCH_LIMIT,
                    filter=query_filter,
                ),
            ],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        return [
            SearchHit(clause=Clause.model_validate(point.payload), score=point.score)
            for point in response.points
            if point.payload is not None
        ]

    def count(self) -> int:
        """Number of indexed points."""
        return self._client.count(collection_name=self._collection, exact=True).count
