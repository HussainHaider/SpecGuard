"""PgVectorStore's contract, as far as it can be checked without a database.

The interesting half of this store is SQL and needs Postgres, which the default suite may
not have. What is checked here is what a live test would not catch anyway: that it
satisfies the protocol, that its identifiers cannot carry SQL, and that it fuses on the
same terms as Qdrant so the comparison is between the stores rather than between two
different constants.
"""

from __future__ import annotations

import pytest

from specguard.embedding.encoder import Encoder
from specguard.models.common import Language
from specguard.vectorstore import qdrant as qdrant_store
from specguard.vectorstore.pgvector import RRF_K, TS_CONFIG, PgVectorStore, _vector_literal
from specguard.vectorstore.protocol import VectorStore


class TestContract:
    def test_it_satisfies_the_vector_store_protocol(self):
        assert isinstance(PgVectorStore(None, Encoder()), VectorStore)

    def test_it_prefetches_as_deeply_as_qdrant_does(self):
        """Fusion can only reorder what each branch returned, so the depth has to match."""
        from specguard.vectorstore.pgvector import PREFETCH_LIMIT

        assert PREFETCH_LIMIT == qdrant_store.PREFETCH_LIMIT

    def test_it_fuses_on_the_same_constant_the_paper_and_qdrant_use(self):
        assert RRF_K == 60


class TestIdentifierSafety:
    def test_a_table_name_that_is_not_an_identifier_is_refused(self):
        # The table name is interpolated into DDL, which cannot be parameterised.
        with pytest.raises(ValueError, match="not a valid table name"):
            PgVectorStore(None, Encoder(), table="clauses; DROP TABLE jobs")

    def test_a_plain_identifier_is_accepted(self):
        assert PgVectorStore(None, Encoder(), table="clause_vectors_v2") is not None


class TestLanguageHandling:
    def test_each_indexed_language_has_its_own_text_search_configuration(self):
        """Without this every German clause is stemmed by the English snowball stemmer."""
        assert TS_CONFIG[Language.EN] == "english"
        assert TS_CONFIG[Language.DE] == "german"


class TestVectorLiteral:
    def test_it_renders_a_pgvector_literal(self):
        assert _vector_literal([1.0, -0.5]) == "[1.0000000,-0.5000000]"

    def test_an_empty_vector_is_still_well_formed(self):
        assert _vector_literal([]) == "[]"
