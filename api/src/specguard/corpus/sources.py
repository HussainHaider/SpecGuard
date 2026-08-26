"""The documents that make up the knowledge base.

Consolidated CELEX ids are pinned deliberately. A consolidated act is immutable — when
the EU consolidates again it gets a new id — so pinning is what makes ``source_version``,
and therefore every chunk id and every stored citation, stable over time.
"""

from __future__ import annotations

from specguard.models.common import Language, SpecGuardModel


class SourceSpec(SpecGuardModel):
    """One act to ingest, in every language we index."""

    celex: str
    regulation: str
    languages: tuple[Language, ...]


#: Cellar wants ISO 639-2/B codes; the rest of the project speaks ISO 639-1.
CELLAR_LANGUAGE_CODES: dict[Language, str] = {
    Language.EN: "eng",
    Language.DE: "deu",
    Language.FR: "fra",
    Language.ES: "spa",
    Language.NL: "nld",
}

INDEXED_LANGUAGES: tuple[Language, ...] = (Language.EN, Language.DE)

SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        celex="02011R1169-20180101",
        regulation="Regulation (EU) No 1169/2011",
        languages=INDEXED_LANGUAGES,
    ),
    SourceSpec(
        celex="02006R1924-20141213",
        regulation="Regulation (EC) No 1924/2006",
        languages=INDEXED_LANGUAGES,
    ),
)


def raw_filename(celex: str, language: Language) -> str:
    """Filename for one language version of one act under ``corpus/raw/``."""
    return f"{celex}-{language.value}.txt"


def source_version_for(regulation: str, language: Language) -> str:
    """The ``source_version`` of one act's text in one language.

    Chunk ids are salted with this, so a citation built without it points at nothing.
    Each act has its own consolidation date, so there is no single corpus-wide version
    to fall back on.
    """
    for source in SOURCES:
        if source.regulation == regulation:
            return f"{source.celex}-{language.value}"
    raise KeyError(f"{regulation!r} is not an indexed act")
