"""Chunking is where citations get their identity, so its guarantees are tested here."""

from __future__ import annotations

from pathlib import Path

import pytest

from specguard.models.common import Language
from specguard.models.corpus import CorpusDocument
from specguard.retrieval.chunking import ChunkingError, chunk_document

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
REGULATION = "Regulation (EU) No 1169/2011"
CELEX = "02011R1169-20180101"


def _load(language: Language):
    path = CORPUS / "raw" / f"{CELEX}-{language.value}.txt"
    if not path.exists():
        pytest.skip("corpus not fetched; run python -m specguard.corpus.fetch")
    return chunk_document(
        path.read_text(encoding="utf-8"),
        regulation=REGULATION,
        celex=CELEX,
        language=language,
        source_version=f"{CELEX}-{language.value}",
    )


@pytest.fixture(scope="module")
def english():
    return _load(Language.EN)


class TestChunkIdentity:
    def test_chunk_ids_are_unique(self, english) -> None:
        # A collision means one clause overwrites another in Qdrant and a stored
        # citation silently resolves to the wrong text.
        ids = [clause.chunk_id for clause in english]
        assert len(set(ids)) == len(ids)

    def test_reparsing_reproduces_identical_ids(self, english) -> None:
        # The whole Postgres/Qdrant split rests on this: citations stored today must
        # still resolve against an index rebuilt tomorrow.
        again = _load(Language.EN)
        assert [c.chunk_id for c in again] == [c.chunk_id for c in english]

    def test_language_versions_do_not_collide(self, english) -> None:
        german = {clause.chunk_id for clause in _load(Language.DE)}
        assert not german & {clause.chunk_id for clause in english}


class TestLegalStructure:
    @pytest.mark.parametrize(
        ("article", "paragraph"),
        [("9", "1"), ("21", "1"), ("22", "1"), ("26", "3"), ("32", "2")],
    )
    def test_rule_anchors_are_present(self, english, article, paragraph) -> None:
        # Every clause the eight rules cite has to survive chunking, or the rule that
        # depends on it cannot produce a citation at all.
        assert any(c.article == article and c.paragraph == paragraph for c in english)

    def test_annexes_are_chunked(self, english) -> None:
        annexes = {c.article for c in english if c.article.startswith("Annex")}
        assert {"Annex II", "Annex VI", "Annex VII", "Annex XIV"} <= annexes

    def test_annex_parts_are_distinguished(self, english) -> None:
        # Annexes VI and VII restart numbering per part; without the part in the
        # locator, Part A.1 and Part B.1 are the same clause.
        locators = {c.paragraph for c in english if c.article == "Annex VI"}
        assert {"Part A.1", "Part B.1"} <= locators

    def test_numbered_locators_are_language_independent(self, english) -> None:
        # Article and annex numbers are normalised, so "Art. 9(1)" is the same locator
        # in both languages. Sub-heading locators are the heading text itself and are
        # language-specific by construction, so they are compared by count, not value.
        def numbered(clauses):
            return {
                (c.article, c.paragraph)
                for c in clauses
                if c.paragraph is None or c.paragraph[0].isdigit() or c.paragraph.startswith("Part")
            }

        german_clauses = _load(Language.DE)
        assert numbered(english) == numbered(german_clauses)
        assert len(english) == len(german_clauses)

    def test_conversion_factors_survive_as_text(self, english) -> None:
        # Annex XIV is a table in the source markup, and NUTRITION_ARITHMETIC has no
        # legal basis to cite if the table is dropped.
        annex = [c for c in english if c.article == "Annex XIV"]
        assert any("37 kJ/g" in c.text for c in annex)

    def test_headings_are_carried_into_the_embedding_text(self, english) -> None:
        clause = next(c for c in english if c.article == "32" and c.paragraph == "2")
        assert clause.heading is not None
        assert clause.heading in clause.embedding_text


class TestFailureModes:
    def test_unparseable_text_raises_rather_than_indexing_nothing(self) -> None:
        with pytest.raises(ChunkingError):
            chunk_document(
                "This document has no articles at all.",
                regulation=REGULATION,
                celex=CELEX,
                language=Language.EN,
                source_version="v1",
            )

    def test_source_version_identifies_the_language(self) -> None:
        document = CorpusDocument(
            celex=CELEX,
            regulation=REGULATION,
            language=Language.DE,
            sha256="0" * 64,
            fetched_at="2026-01-01T00:00:00Z",
            url="https://example.invalid",
        )
        assert document.source_version == f"{CELEX}-de"
