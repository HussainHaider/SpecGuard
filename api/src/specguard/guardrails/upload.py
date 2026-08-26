"""Input gates: what we will accept as a document at all.

The cheapest guardrail in the system. Rejecting a 400 MB file or a ZIP renamed to .pdf
costs microseconds; discovering the problem after ingestion, extraction and eight rules
costs a model call and a confusing report.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 40

#: Every PDF begins with this. Checked instead of trusting the filename or the declared
#: content type, both of which are supplied by the caller and neither of which is
#: evidence of anything.
_PDF_MAGIC = b"%PDF-"


class UploadRejectedError(ValueError):
    """The upload cannot be accepted. The message is shown to the caller."""


@dataclass(frozen=True)
class UploadLimits:
    """Configurable bounds, so a deployment can tighten them without a code change."""

    max_bytes: int = MAX_UPLOAD_BYTES
    max_pages: int = MAX_PAGES


def check_upload(payload: bytes, filename: str, limits: UploadLimits | None = None) -> None:
    """Raise UploadRejectedError unless this looks like a PDF we are willing to process."""
    bounds = limits or UploadLimits()

    if not payload:
        raise UploadRejectedError("The uploaded file is empty.")

    if len(payload) > bounds.max_bytes:
        raise UploadRejectedError(
            f"The file is {len(payload) / 1_048_576:.1f} MB, above the "
            f"{bounds.max_bytes / 1_048_576:.0f} MB limit."
        )

    if not payload.startswith(_PDF_MAGIC):
        raise UploadRejectedError(
            f"{filename!r} is not a PDF. Only PDF specification sheets are accepted."
        )


def check_page_count(pages: int, limits: UploadLimits | None = None) -> None:
    """Raise UploadRejectedError if the document is longer than we will read.

    Separate from ``check_upload`` because the page count is only known after parsing,
    and parsing is what we are trying to avoid doing on a hostile file.
    """
    bounds = limits or UploadLimits()
    if pages > bounds.max_pages:
        raise UploadRejectedError(
            f"The document has {pages} pages, above the {bounds.max_pages} page limit. "
            "Specification sheets are normally one or two pages."
        )
