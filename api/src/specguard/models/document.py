"""The output of ingestion: page text with the layout hints the rules need.

Emphasis is the reason this model exists. Art. 21(1)(b) is a claim about *typeset* — an
allergen must be distinguished from the rest of the ingredient list — so an ingestion
step that returns flat text has already destroyed the evidence ALLERGEN_EMPHASIS needs.
Font weight and capitalisation are carried through to the extractor.
"""

from __future__ import annotations

from pydantic import Field

from specguard.models.common import SpecGuardModel
from specguard.models.spec import EmphasisStyle, SourceDocument


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
    def is_upper(self) -> bool:
        """Whether the word is set in capitals, ignoring punctuation and short tokens."""
        letters = [c for c in self.text if c.isalpha()]
        return len(letters) > 1 and all(c.isupper() for c in letters)

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
        """Normalised words carrying emphasis within ``phrase``.

        Empty when the phrase cannot be located, which the caller must treat as "not
        known" rather than "not emphasised" — absence of evidence is a NEEDS_REVIEW.
        """
        return {
            _normalise(span.text)
            for span in self.spans_for(phrase)
            if span.emphasis is not EmphasisStyle.NONE
        }
