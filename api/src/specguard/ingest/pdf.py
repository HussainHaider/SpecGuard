"""Read a supplier PDF into text plus the layout hints the rules depend on."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pdfplumber

from specguard.models.document import IngestedDocument, Page, TextSpan
from specguard.models.spec import SourceDocument

#: pdfplumber reports the embedded font name, e.g. "ABCDEF+Helvetica-Bold". Weight and
#: slant have to be read out of that string — there is no structured attribute for them.
_BOLD_MARKERS = ("bold", "black", "heavy", "semib", "-bd")
_ITALIC_MARKERS = ("italic", "oblique", "-it")


def _has_marker(font: str, markers: tuple[str, ...]) -> bool:
    lowered = font.lower()
    return any(marker in lowered for marker in markers)


class EmptyDocumentError(ValueError):
    """Raised when a PDF yields no text at all.

    Almost always a scanned label photographed rather than a spec sheet. OCR is out of
    scope, so this surfaces as an error the graph turns into NEEDS_REVIEW rather than
    something that quietly produces an empty ProductSpec.
    """


def ingest_pdf(path: Path) -> IngestedDocument:
    """Read a PDF into pages of text and typographically annotated spans."""
    payload = path.read_bytes()
    pages: list[Page] = []

    with pdfplumber.open(path) as document:
        for index, page in enumerate(document.pages, start=1):
            words = page.extract_words(extra_attrs=["fontname", "size"]) or []
            spans = [
                TextSpan(
                    text=word["text"],
                    page=index,
                    font=word.get("fontname", ""),
                    size=float(word.get("size", 0.0)),
                    bold=_has_marker(word.get("fontname", ""), _BOLD_MARKERS),
                    italic=_has_marker(word.get("fontname", ""), _ITALIC_MARKERS),
                )
                for word in words
            ]
            pages.append(Page(number=index, text=page.extract_text() or "", spans=spans))

    source = SourceDocument(
        filename=path.name,
        sha256=hashlib.sha256(payload).hexdigest(),
        page_count=max(len(pages), 1),
        byte_size=len(payload),
    )
    ingested = IngestedDocument(source=source, pages=pages)

    if not ingested.text.strip():
        raise EmptyDocumentError(
            f"{path.name} yielded no extractable text; it is likely a scanned image, "
            "and OCR is out of scope"
        )
    return ingested
