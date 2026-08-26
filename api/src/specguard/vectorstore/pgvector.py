"""Postgres-backed vector store: pgvector for dense, tsvector for lexical, RRF in SQL.

This exists because the comparison is a deliverable, not because the application needs a
second backend (CLAUDE.md, on the one sanctioned abstraction). The question it answers is
narrow and worth answering honestly: for a corpus of 734 clauses, does the dedicated
vector database earn the extra service?

Three differences from the Qdrant implementation are structural rather than incidental,
and they are the substance of the comparison:

* **The sparse side is not the same retriever.** Qdrant stores a bm25 vector produced by
  the same fastembed model that encodes everything else. Postgres has no bm25; it has
  ``ts_rank_cd`` over a ``tsvector``, with its own stemming and its own weighting. So this
  is not "the same hybrid search on different storage" — the lexical half is a different
  algorithm, and any difference in recall is partly that.
* **Fusion is ours.** Qdrant fuses server-side and the project forbids reimplementing it
  above the line. Postgres has no fusion primitive, so RRF is written out in SQL here.
  That is not a violation of the rule, it is the rule's cost made visible.
* **Language is a column and a regconfig.** The corpus is English and German, and
  ``to_tsvector`` needs to know which — so the text search configuration is chosen per
  language rather than defaulting to English and stemming German badly.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from specguard.embedding.encoder import Encoder
from specguard.models.common import Language
from specguard.models.corpus import Clause
from specguard.vectorstore.protocol import SearchHit

DEFAULT_TABLE = "clause_vectors"
PREFETCH_LIMIT = 50
DEFAULT_LIMIT = 5

#: The constant in reciprocal rank fusion. 60 is the value from the original paper and
#: the one Qdrant uses, so the two stores are fused on the same terms.
RRF_K = 60

#: Postgres text search configurations, per indexed language. Without this every German
#: clause would be stemmed by the English snowball stemmer.
TS_CONFIG: dict[Language, str] = {Language.EN: "english", Language.DE: "german"}


def _vector_literal(values: Sequence[float]) -> str:
    """A dense vector as a pgvector literal.

    Passed as text and cast in SQL, so this needs no client-side type adapter and the
    project takes no new dependency for the sake of one comparison.
    """
    return "[" + ",".join(f"{value:.7f}" for value in values) + "]"


class PgVectorStore:
    """Clause storage and hybrid retrieval in one Postgres table.

    Takes a psycopg connection rather than opening its own: the benchmark wants to
    control the connection's lifetime, and a store that owns a pool is a store that has
    opinions about how the process is structured.
    """

    def __init__(
        self,
        connection: Any,
        encoder: Encoder,
        table: str = DEFAULT_TABLE,
    ) -> None:
        self._connection = connection
        self._encoder = encoder
        # Interpolated into DDL, which cannot be parameterised. Kept to an identifier so
        # a caller cannot smuggle SQL in through a table name.
        if not table.replace("_", "").isalnum():
            raise ValueError(f"{table!r} is not a valid table name")
        self._table = table

    def _ensure_table(self) -> None:
        """Create the extension, table and indexes if they are not already there."""
        with self._connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    chunk_id       uuid PRIMARY KEY,
                    language       text NOT NULL,
                    regulation     text NOT NULL,
                    source_version text NOT NULL,
                    text           text NOT NULL,
                    payload        jsonb NOT NULL,
                    embedding      vector({self._encoder.dimensions}) NOT NULL
                )
            """)
            # HNSW rather than IVFFlat: IVFFlat needs a populated table before its lists
            # can be built sensibly, which makes a fresh index quietly bad at recall.
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self._table}_embedding_idx "
                f"ON {self._table} USING hnsw (embedding vector_cosine_ops)"
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self._table}_lang_idx ON {self._table} (language)"
            )
        self._connection.commit()

    def upsert(self, clauses: Sequence[Clause]) -> int:
        """Index clauses. Idempotent: the chunk id is the primary key."""
        self._ensure_table()
        if not clauses:
            return 0

        texts = [clause.embedding_text for clause in clauses]
        dense = self._encoder.encode_passages(texts)

        rows = [
            (
                clause.chunk_id,
                clause.language.value,
                clause.regulation,
                clause.source_version,
                clause.text,
                json.dumps(clause.model_dump(mode="json"), ensure_ascii=False),
                _vector_literal(vector),
            )
            for clause, vector in zip(clauses, dense, strict=True)
        ]
        with self._connection.cursor() as cursor:
            cursor.executemany(
                f"""
                INSERT INTO {self._table}
                    (chunk_id, language, regulation, source_version, text, payload, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    language = EXCLUDED.language,
                    regulation = EXCLUDED.regulation,
                    source_version = EXCLUDED.source_version,
                    text = EXCLUDED.text,
                    payload = EXCLUDED.payload,
                    embedding = EXCLUDED.embedding
                """,  # noqa: S608 - table name is validated as an identifier above
                rows,
            )
        self._connection.commit()
        return len(rows)

    def search(
        self,
        query: str,
        *,
        language: Language,
        limit: int = DEFAULT_LIMIT,
        regulation: str | None = None,
    ) -> list[SearchHit]:
        """Hybrid search: dense KNN and lexical rank, fused with RRF in one statement.

        One round trip. Fusing in Python would mean paging both candidate sets back over
        the wire to recompute what the database can compute while it still has them.
        """
        config = TS_CONFIG.get(language, "simple")
        embedding = _vector_literal(self._encoder.encode_query(query))

        sql = f"""
            WITH filtered AS (
                SELECT * FROM {self._table}
                WHERE language = %(language)s
                  AND (%(regulation)s::text IS NULL OR regulation = %(regulation)s)
            ),
            dense AS (
                SELECT chunk_id,
                       row_number() OVER (ORDER BY embedding <=> %(embedding)s::vector) AS rank
                FROM filtered
                ORDER BY embedding <=> %(embedding)s::vector
                LIMIT %(prefetch)s
            ),
            lexical AS (
                SELECT chunk_id,
                       row_number() OVER (
                           ORDER BY ts_rank_cd(
                               to_tsvector('{config}', text),
                               websearch_to_tsquery('{config}', %(query)s)
                           ) DESC
                       ) AS rank
                FROM filtered
                WHERE to_tsvector('{config}', text)
                      @@ websearch_to_tsquery('{config}', %(query)s)
                LIMIT %(prefetch)s
            ),
            fused AS (
                SELECT chunk_id, SUM(score) AS score FROM (
                    SELECT chunk_id, 1.0 / (%(k)s + rank) AS score FROM dense
                    UNION ALL
                    SELECT chunk_id, 1.0 / (%(k)s + rank) AS score FROM lexical
                ) parts
                GROUP BY chunk_id
            )
            SELECT f.payload, fused.score
            FROM fused JOIN filtered f USING (chunk_id)
            ORDER BY fused.score DESC
            LIMIT %(limit)s
        """  # noqa: S608 - table name and regconfig are both validated values, not input

        with self._connection.cursor() as cursor:
            cursor.execute(
                sql,
                {
                    "language": language.value,
                    "regulation": regulation,
                    "embedding": embedding,
                    "query": query,
                    "prefetch": PREFETCH_LIMIT,
                    "limit": limit,
                    "k": RRF_K,
                },
            )
            rows = cursor.fetchall()

        return [
            SearchHit(clause=Clause.model_validate(payload), score=float(score))
            for payload, score in rows
        ]

    def reset(self) -> None:
        """Drop the table and everything in it."""
        with self._connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {self._table}")
        self._connection.commit()
