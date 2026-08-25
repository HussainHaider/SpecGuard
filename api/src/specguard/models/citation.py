"""Citations into the regulation corpus, and the deterministic chunk id they hang on."""

from __future__ import annotations

import re
import uuid
from typing import Self

from pydantic import Field, model_validator

from specguard.models.common import SpecGuardModel

# Fixed namespace for SpecGuard chunk ids. Changing this constant invalidates every
# citation ever stored in Postgres, so it is frozen for the life of the project.
CHUNK_ID_NAMESPACE = uuid.UUID("6f0a1d54-9c3e-5b7a-9f2c-2d1b8e4a7c33")

_WHITESPACE = re.compile(r"\s+")


def _canonical(part: str | None) -> str:
    """Normalise one component of a clause reference so ids survive cosmetic drift."""
    if part is None:
        return ""
    return _WHITESPACE.sub(" ", part).strip().casefold()


def chunk_id_for(
    regulation: str,
    article: str,
    paragraph: str | None,
    source_version: str,
) -> str:
    """Derive the deterministic chunk id for a clause.

    The id is a UUIDv5 over the canonicalised ``regulation|article|paragraph|version``
    tuple. Two properties make the Postgres/Qdrant split safe:

    1. Re-indexing the corpus reproduces byte-identical ids, so citations already
       written to Postgres still resolve against a freshly built Qdrant collection.
    2. A UUID is a legal Qdrant point id, so the chunk id *is* the point id — no
       side table mapping our ids onto Qdrant's.

    ``article`` carries the clause locator and may name an annex ("Annex II"); the
    point/sub-point ("(1)(b)") is display detail and deliberately does not affect
    the id, because chunking is at paragraph granularity.
    """
    canonical = "|".join(
        (
            _canonical(regulation),
            _canonical(article),
            _canonical(paragraph),
            _canonical(source_version),
        )
    )
    return str(uuid.uuid5(CHUNK_ID_NAMESPACE, canonical))


class Citation(SpecGuardModel):
    """A pointer to the clause that justifies a verdict.

    Non-negotiable #1 says no verdict without a resolvable citation, and "resolvable"
    is enforced here: ``chunk_id`` must equal ``chunk_id_for()`` over this citation's
    own fields, so a citation cannot claim an article it did not actually retrieve.
    """

    regulation: str = Field(
        min_length=1,
        description='Short regulation identifier, e.g. "Regulation (EU) No 1169/2011".',
    )
    article: str = Field(
        min_length=1,
        description='Clause locator: an article number ("9", "32") or an annex ("Annex II").',
    )
    paragraph: str | None = Field(
        default=None,
        description='Paragraph within the article, e.g. "2". None for whole-article chunks.',
    )
    point: str | None = Field(
        default=None,
        description='Sub-point for display only, e.g. "b" in Art. 21(1)(b). Not part of chunk_id.',
    )
    chunk_id: str = Field(
        description="Deterministic UUIDv5 chunk id; also the Qdrant point id.",
    )
    quoted_span: str = Field(
        min_length=1,
        description="Verbatim text from the cited chunk. Must appear in the chunk it names.",
    )
    source_version: str = Field(
        min_length=1,
        description='Corpus version this citation was produced against, e.g. "2024-11-01".',
    )
    eurlex_url: str | None = Field(
        default=None,
        description="Deep link to the consolidated text on EUR-Lex, for the reader.",
    )
    retrieval_score: float | None = Field(
        default=None,
        description="Fused RRF score of the chunk. None for deterministic rules, which "
        "cite a fixed clause rather than retrieving one.",
    )

    @model_validator(mode="after")
    def _chunk_id_matches_clause(self) -> Self:
        expected = chunk_id_for(self.regulation, self.article, self.paragraph, self.source_version)
        if self.chunk_id != expected:
            raise ValueError(
                f"chunk_id {self.chunk_id!r} does not match the clause it cites "
                f"({self.regulation} {self.article} para={self.paragraph} "
                f"v={self.source_version} -> {expected!r})"
            )
        return self

    @classmethod
    def for_clause(
        cls,
        *,
        regulation: str,
        article: str,
        quoted_span: str,
        source_version: str,
        paragraph: str | None = None,
        point: str | None = None,
        eurlex_url: str | None = None,
        retrieval_score: float | None = None,
    ) -> Citation:
        """Build a citation, deriving ``chunk_id`` rather than trusting a caller with it."""
        return cls(
            regulation=regulation,
            article=article,
            paragraph=paragraph,
            point=point,
            chunk_id=chunk_id_for(regulation, article, paragraph, source_version),
            quoted_span=quoted_span,
            source_version=source_version,
            eurlex_url=eurlex_url,
            retrieval_score=retrieval_score,
        )

    @property
    def reference(self) -> str:
        """Human-readable reference, e.g. ``Regulation (EU) No 1169/2011 Art. 21(1)(b)``."""
        if self.article.casefold().startswith("annex"):
            locator = self.article
        else:
            locator = f"Art. {self.article}"
            if self.paragraph is not None:
                locator += f"({self.paragraph})"
        if self.point is not None:
            locator += f"({self.point})"
        return f"{self.regulation} {locator}"
