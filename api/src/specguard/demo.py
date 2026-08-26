"""Demo mode: replay a stored report instead of running the check.

The deployed instance is public. Running the real graph there would mean a stranger's
upload spending money on model calls, and a queue anyone can fill — so the public
deployment serves reports that were computed here, from documents authored here, and
says so on every one of them.

Two properties this has to keep, or it is worse than not existing:

* **It never runs a model or an embedding.** Not "usually does not" — the demo path does
  not construct a client or a vector store at all, so there is no configuration under
  which a demo deployment quietly starts spending.
* **A replayed report is labelled as replayed.** The report carries the flag, the API
  returns it, and the UI shows it. A demo that looks like a live result is a lie told to
  whoever is evaluating the system.

Matching is by content hash. Upload the fixture spec and you get the report that was
computed from that exact document; upload anything else and demo mode says it has no
report for it rather than returning somebody else's verdicts.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from specguard.config import get_settings
from specguard.models.report import CheckReport


def report_dir() -> Path:
    """Where the pre-computed reports live.

    Read from settings rather than derived from ``__file__``: inside the container the
    package sits at /app/src/specguard and walking up lands on /, which is not where the
    fixtures are mounted. Committed to the repository, so a clean clone can run demo
    mode with no services beyond the API itself.
    """
    return get_settings().fixtures_dir / "reports"


#: Recorded on the report so every consumer can see what it is looking at.
DEMO_NOTE = (
    "Replayed result. This report was computed in advance from a synthetic "
    "specification authored in this repository; no model was called to produce it."
)


class NoDemoReportError(LookupError):
    """This document has no pre-computed report.

    Raised rather than falling back to any other report: returning verdicts computed
    from a different document would be the worst possible failure for a compliance tool
    to have, demo or not.
    """


@lru_cache(maxsize=1)
def _index() -> dict[str, Path]:
    """sha256 of the source PDF to the report computed from it."""
    directory = report_dir()
    if not directory.exists():
        return {}
    index: dict[str, Path] = {}
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        digest = payload.get("spec", {}).get("source", {}).get("sha256")
        if digest:
            index[digest] = path
    return index


def available() -> int:
    """How many documents demo mode can answer for."""
    return len(_index())


def report_for(payload: bytes) -> CheckReport:
    """The pre-computed report for this exact document."""
    digest = hashlib.sha256(payload).hexdigest()
    path = _index().get(digest)
    if path is None:
        raise NoDemoReportError(
            "This deployment is running in demo mode and can only replay reports for "
            "the sample specifications published with it. Pick one of those to see a "
            "full report."
        )

    stored = json.loads(path.read_text(encoding="utf-8"))
    report = CheckReport.model_validate(stored)
    # Stamped at read time rather than baked into the file, so the same fixtures are
    # usable by the eval harness without carrying a demo flag around.
    return report.model_copy(update={"demo": True, "demo_note": DEMO_NOTE})
