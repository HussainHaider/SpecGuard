"""Turn a plain-text EUR-Lex export into clause chunks.

The chunk boundary is the legal structure — an article paragraph, a numbered annex
entry, or a named set of conditions of use — never a character budget. That is not a
stylistic preference: a citation has to name an article and a paragraph, so a chunk that
straddles two paragraphs cannot be cited honestly, and one that splits a paragraph in
half is incomplete evidence by construction. Structure-aligned chunks are what make
``chunk_id_for()`` meaningful and every retrieval hit directly citable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from specguard.models.common import Language
from specguard.models.corpus import Clause


@dataclass(frozen=True)
class _Grammar:
    """The surface forms that differ between language versions of an act."""

    article: re.Pattern[str]
    annex: re.Pattern[str]
    part: re.Pattern[str]
    paragraph: re.Pattern[str]
    annex_paragraph: re.Pattern[str]


#: German numbers article paragraphs "(1)" but annex entries "1.", while English uses
#: "1." for both. The two are kept apart deliberately: accepting either form everywhere
#: makes a German article's paragraph "(1)" and its internal list item "1." collide on
#: the same chunk id.
_GRAMMARS: dict[Language, _Grammar] = {
    Language.EN: _Grammar(
        article=re.compile(r"^Article\s+(\d+[a-z]?)$", re.IGNORECASE),
        annex=re.compile(r"^ANNEX(?:\s+([IVXL]+[A-Za-z]?))?$"),
        part=re.compile(r"^PART\s+([A-Z])\b"),
        paragraph=re.compile(r"^(\d+)\.\s+"),
        annex_paragraph=re.compile(r"^(\d+)\.\s+"),
    ),
    Language.DE: _Grammar(
        article=re.compile(r"^Artikel\s+(\d+[a-z]?)$", re.IGNORECASE),
        annex=re.compile(r"^ANHANG(?:\s+([IVXL]+[A-Za-z]?))?$"),
        part=re.compile(r"^TEIL\s+([A-Z])\b"),
        paragraph=re.compile(r"^\((\d+)\)\s+"),
        annex_paragraph=re.compile(r"^(\d+)\.\s+"),
    ),
}

#: A short all-caps line inside an annex is a sub-heading. In Regulation 1924/2006 that
#: is how each nutrition claim's conditions of use are separated ("LOW FAT", "SOURCE OF
#: FIBRE"), and those headings are the natural chunk boundary for the claim rules.
_MAX_SUBHEADING_CHARS = 90


@dataclass
class _Section:
    """The article or annex currently being accumulated."""

    article: str
    heading: str | None = None
    paragraph: str | None = None
    part: str | None = None
    part_title: str | None = None
    subheading: str | None = None
    lines: list[str] = field(default_factory=list)
    seen_heading: bool = False


class ChunkingError(ValueError):
    """Raised when a document yields no clauses — a silently empty index is worse."""


def _is_subheading(block: str) -> bool:
    """Whether a block is an all-caps sub-heading rather than legal text."""
    if "\n" in block or len(block) > _MAX_SUBHEADING_CHARS:
        return False
    letters = [c for c in block if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _blocks(text: str) -> list[str]:
    return [block.strip() for block in text.split("\n\n") if block.strip()]


def chunk_document(
    text: str,
    *,
    regulation: str,
    celex: str,
    language: Language,
    source_version: str,
) -> list[Clause]:
    """Parse one plain-text act into clause chunks.

    Blocks carrying no locator of their own are appended to the clause they follow.
    Subparagraphs, lettered points and tables belong to the paragraph that introduces
    them, and detaching them would strand evidence from the rule it supports.
    """
    grammar = _GRAMMARS[language]
    clauses: list[Clause] = []
    section: _Section | None = None

    def flush() -> None:
        if section is None:
            return
        body = "\n".join(section.lines).strip()
        section.lines = []
        if not body:
            return
        heading_parts = [part for part in (section.heading, section.part_title) if part]
        # A part's unnumbered lead-in still needs a locator of its own: Annex VII has
        # three of them (Parts A, B and C) and they would otherwise all be paragraph
        # None, collide on one chunk id, and leave two thirds of the annex unindexed.
        part_locator = f"Part {section.part}" if section.part else None
        paragraph = section.paragraph or section.subheading or part_locator
        clauses.append(
            Clause.build(
                regulation=regulation,
                celex=celex,
                article=section.article,
                paragraph=paragraph,
                heading=" — ".join(heading_parts) or None,
                language=language,
                source_version=source_version,
                text=body,
            )
        )

    for block in _blocks(text):
        first_line = block.split("\n", 1)[0]

        if article_match := grammar.article.match(first_line):
            flush()
            # Locators are normalised to their language-independent form, so English and
            # German citations to the same clause read identically in a report.
            section = _Section(article=article_match.group(1))
            continue

        if annex_match := grammar.annex.match(first_line):
            flush()
            number = annex_match.group(1)
            section = _Section(article=f"Annex {number.upper()}" if number else "Annex")
            continue

        if section is None:
            # Recitals, the preamble and the signature block: real text, but nothing a
            # rule can cite, so deliberately not indexed.
            continue

        in_annex = section.article.startswith("Annex")

        if in_annex and (part_match := grammar.part.match(first_line)):
            # Annexes VI and VII restart numbering inside each part, so "1." means two
            # different things in Part A and Part B. Without the part in the paragraph
            # both hash to the same chunk id and one silently overwrites the other.
            flush()
            section.part = part_match.group(1)
            section.part_title = first_line
            section.paragraph = None
            section.subheading = None
            continue

        pattern = grammar.annex_paragraph if in_annex else grammar.paragraph
        if paragraph_match := pattern.match(block):
            flush()
            number = paragraph_match.group(1)
            section.paragraph = f"Part {section.part}.{number}" if section.part else number
            section.lines = [block]
            continue

        if not section.seen_heading:
            section.heading = block
            section.seen_heading = True
            continue

        if in_annex and _is_subheading(block):
            flush()
            section.subheading = block
            section.paragraph = None
            continue

        section.lines.append(block)

    flush()

    if not clauses:
        raise ChunkingError(
            f"{celex} ({language.value}) produced no clauses; the export's structure did "
            "not match the expected article and paragraph grammar"
        )
    return clauses
