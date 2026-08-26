"""The output of ingestion: page text with the layout hints the rules need.

Emphasis is the reason this model exists. Art. 21(1)(b) is a claim about *typeset* — an
allergen must be distinguished from the rest of the ingredient list — so an ingestion
step that returns flat text has already destroyed the evidence ALLERGEN_EMPHASIS needs.
Font weight and capitalisation are carried through to the extractor.
"""

from __future__ import annotations

import re

from pydantic import Field

from specguard.models.common import SpecGuardModel
from specguard.models.spec import EmphasisStyle, SourceDocument


def _caps_runs(text: str) -> set[str]:
    """Upper-case letter runs of length two or more, anywhere in the text."""
    return set(re.findall(r"[A-ZÄÖÜÀ-Þ]{2,}", text))


def _normalise(text: str) -> str:
    """Strip punctuation and case so span text can be matched against extracted text."""
    return "".join(c for c in text if c.isalnum()).upper()


class TextSpan(SpecGuardModel):
    """One word, with the typographic facts about it."""

    text: str
    page: int = Field(ge=1)
    font: str
    size: float
    bold: bool
    italic: bool

    @property
    def capitalised_runs(self) -> set[str]:
        """Runs of two or more consecutive capitals inside this word.

        Emphasis is often applied to part of a word, not all of it: "SOJA-Lecithin",
        "WEIZEN-Vollkornmehl", "MILCH-Erzeugnis". Treating only fully upper-case tokens
        as emphasised misses exactly the compound forms German labelling relies on,
        while a one-letter run would match every ordinary capitalised noun.
        """
        return _caps_runs(self.text)

    @property
    def is_upper(self) -> bool:
        """Whether the word carries capitalised emphasis."""
        return bool(self.capitalised_runs)

    @property
    def emphasis(self) -> EmphasisStyle:
        """How this word is emphasised, if at all."""
        if self.bold:
            return EmphasisStyle.BOLD
        if self.is_upper:
            return EmphasisStyle.UPPERCASE
        if self.italic:
            return EmphasisStyle.ITALIC
        return EmphasisStyle.NONE


class Page(SpecGuardModel):
    """One page: its plain text and the spans it was assembled from."""

    number: int = Field(ge=1)
    text: str
    spans: list[TextSpan] = Field(default_factory=list)


class IngestedDocument(SpecGuardModel):
    """A supplier PDF, read.

    Everything in here is untrusted. The text may contain instructions aimed at the
    model, and nothing downstream may treat it as anything but data.
    """

    source: SourceDocument
    pages: list[Page]

    @property
    def text(self) -> str:
        """The whole document as plain text, pages separated by a form feed."""
        return "\n\f\n".join(page.text for page in self.pages)

    @property
    def spans(self) -> list[TextSpan]:
        """Every span in the document, in reading order."""
        return [span for page in self.pages for span in page.spans]

    def spans_for(self, phrase: str) -> list[TextSpan]:
        """The run of spans whose words spell out ``phrase``.

        Emphasis has to be read from the region it was claimed in. Art. 21(1)(b) is
        about an allergen standing out *within the ingredient list*, so a document-wide
        answer would count a bold table label — "Fat", "Energy", the section heading —
        as though it emphasised an ingredient. Locating the ingredient declaration's own
        spans is what makes the check mean what the article means.
        """
        wanted = [_normalise(word) for word in phrase.split() if _normalise(word)]
        if not wanted:
            return []
        spans = self.spans
        words = [_normalise(span.text) for span in spans]
        for start in range(len(words) - len(wanted) + 1):
            if words[start : start + len(wanted)] == wanted:
                return spans[start : start + len(wanted)]
        return []

    def emphasised_words_in(self, phrase: str) -> set[str]:
        """Emphasised tokens within ``phrase``, normalised for comparison.

        A bold word contributes the whole token; a capitalised word contributes its
        capitalised runs, so "SOJA-Lecithin" yields SOJA rather than SOJALECITHIN and
        matches the allergen the emphasis was applied to.

        Empty when the phrase cannot be located, which the caller must treat as "not
        known" rather than "not emphasised" — absence of evidence is a NEEDS_REVIEW.
        """
        found: set[str] = set()
        for span in self.spans_for(phrase):
            if span.emphasis is EmphasisStyle.NONE:
                continue
            found.update(span.capitalised_runs)
            if span.bold:
                found.add(_normalise(span.text))
        return found
