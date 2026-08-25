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
