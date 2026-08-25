"""Corpus records: a document in the knowledge base and a clause chunk within it."""

from __future__ import annotations

import datetime as dt

from pydantic import Field, computed_field

from specguard.models.citation import Citation, chunk_id_for
from specguard.models.common import Language, SpecGuardModel


class CorpusDocument(SpecGuardModel):
    """One language version of one consolidated act.

    ``source_version`` is the document's identity and is what every chunk id derived
    from this document is salted with, so re-fetching an unchanged act reproduces the
    same ids and a new consolidation produces entirely new ones.
    """

    celex: str = Field(
        min_length=1,
        description='Consolidated CELEX id, e.g. "02011R1169-20180101".',
    )
    regulation: str = Field(
        min_length=1, description='Display name, e.g. "Regulation (EU) No 1169/2011".'
    )
    language: Language
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fetched_at: dt.datetime
    url: str = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_version(self) -> str:
        """Identity of this exact text: consolidated act plus language.

        The language belongs here rather than as a fifth component of the chunk id.
        A clause's source is a specific language version of a specific consolidated
        act, and folding it in keeps ``chunk_id_for()`` the four-field derivation that
        the Postgres/Qdrant split is documented to rely on. Without it the English and
        German texts of Article 9 would hash to the same Qdrant point id and silently
        overwrite one another.
        """
        return f"{self.celex}-{self.language.value}"


class Clause(SpecGuardModel):
    """One indexable unit of legal text: an article paragraph, or an annex section.

    The chunk boundary is the legal structure, never a character budget. A paragraph
    is the smallest unit a citation can point at and the largest unit that is still
    about one thing, which is exactly what makes it the right chunk.
    """

    chunk_id: str
    regulation: str = Field(min_length=1)
    celex: str = Field(min_length=1)
    article: str = Field(min_length=1, description='Article number ("9") or annex ("Annex II").')
    paragraph: str | None = Field(
        default=None, description='Paragraph within the article ("1"), or None.'
    )
    heading: str | None = Field(
        default=None, description='Article title, e.g. "List of mandatory particulars".'
    )
    language: Language
    source_version: str = Field(min_length=1)
    text: str = Field(min_length=1)

    @classmethod
    def build(
        cls,
        *,
        regulation: str,
        celex: str,
        article: str,
        paragraph: str | None,
        heading: str | None,
        language: Language,
        source_version: str,
        text: str,
    ) -> Clause:
        """Construct a clause, deriving its chunk id rather than accepting one."""
        return cls(
            chunk_id=chunk_id_for(regulation, article, paragraph, source_version),
            regulation=regulation,
            celex=celex,
            article=article,
            paragraph=paragraph,
            heading=heading,
            language=language,
            source_version=source_version,
            text=text,
        )

    @property
    def embedding_text(self) -> str:
        """What gets embedded: the heading gives an otherwise bare paragraph its subject.

        Article 9(2) reads "The particulars referred to in paragraph 1 shall be
        indicated with words and numbers" — on its own that retrieves for nothing. With
        "Article 9 — List of mandatory particulars" prepended it retrieves for what it
        is actually about.
        """
        locator = self.article if self.article.startswith("Annex") else f"Article {self.article}"
        parts = [locator]
        if self.heading:
            parts.append(self.heading)
        parts.append(self.text)
        return " — ".join(parts)

    def to_citation(self, quoted_span: str, retrieval_score: float | None = None) -> Citation:
        """Build a citation pointing at this clause, for a rule that relied on it."""
        return Citation(
            regulation=self.regulation,
            article=self.article,
            paragraph=self.paragraph,
            chunk_id=self.chunk_id,
            quoted_span=quoted_span,
            source_version=self.source_version,
            retrieval_score=retrieval_score,
        )
