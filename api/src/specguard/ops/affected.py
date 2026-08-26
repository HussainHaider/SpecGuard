"""Which stored checks a corpus change invalidates.

    python -m specguard.ops.affected --json

The regulation watcher re-indexes weekly and then has to decide what to re-run. Re-running
everything is wasteful and, on a real corpus, expensive; re-running nothing makes the
watcher decorative. This answers the question in between.

**The test is the quoted span, not the article number.** A stored citation carries the
exact words the verdict rested on. If those words still appear verbatim in the current
text of the clause it named, the reasoning behind that verdict still stands, whatever else
was renumbered or reformatted around it. If they do not — the clause was amended, or it is
gone — the verdict rests on text that no longer exists and a person should see it again.

Two consequences worth stating. Consolidated acts are immutable, so an amendment arrives
as a new CELEX id and therefore a new ``source_version`` and a new ``chunk_id`` for every
clause. Comparing chunk ids directly would mark every stored check as affected on any
consolidation. Comparing the clause *coordinates* — regulation, article, paragraph — and
then the span, is what makes the answer selective.

And this is derived, never stored. A worklist written down by a previous run would drift
the moment a run failed halfway; recomputing it from the corpus means a re-run that dies
partway is resumed simply by running it again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from specguard.api.deps import get_sessionmaker
from specguard.config import get_settings
from specguard.corpus.seed import load_clauses
from specguard.db.models import Job, JobStatus
from specguard.models.common import Language
from specguard.models.corpus import Clause

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


@dataclass
class Affected:
    """One stored check that a corpus change has undermined."""

    job_id: str
    filename: str
    sha256: str
    reasons: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "sha256": self.sha256,
            "reasons": self.reasons,
        }


def _current_clauses(corpus_dir: Path) -> ClauseIndex:
    """Clauses keyed by (regulation, article, paragraph, language).

    Deliberately not keyed by chunk_id: the id contains ``source_version``, so after a
    consolidation nothing matches and every check would look affected.
    """
    index: ClauseIndex = {}
    for clause in load_clauses(corpus_dir):
        index[(clause.regulation, clause.article, clause.paragraph, clause.language.value)] = clause
    return index


type ClauseIndex = dict[tuple[str, str, str | None, str], Clause]


def assess(report: dict[str, Any], clauses: ClauseIndex) -> list[str]:
    """Why this report is no longer supported by the current corpus. Empty means it is."""
    reasons: list[str] = []
    language = str(report.get("spec", {}).get("language", Language.EN.value))

    for result in report.get("results", []):
        for citation in result.get("citations", []):
            key = (
                citation["regulation"],
                citation["article"],
                citation.get("paragraph"),
                language,
            )
            clause = clauses.get(key)
            reference = f"{citation['regulation']} {citation['article']}"
            if citation.get("paragraph"):
                reference += f"({citation['paragraph']})"

            if clause is None:
                reasons.append(f"{result['rule_id']}: {reference} is no longer in the corpus")
                continue
            if _normalise(citation["quoted_span"]) not in _normalise(clause.text):
                reasons.append(
                    f"{result['rule_id']}: the text relied on in {reference} has changed"
                )
    return reasons


async def find_affected(*, only_passed: bool = True) -> list[Affected]:
    """Stored checks whose citations no longer hold against the current corpus."""
    settings = get_settings()
    clauses = _current_clauses(settings.corpus_dir)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Job).where(Job.status == JobStatus.SUCCEEDED).order_by(Job.created_at)
            )
        ).scalars()

        affected: list[Affected] = []
        for job in rows:
            report = job.report
            if not report:
                continue
            # Only previously-passed checks by default. A check that already failed or
            # abstained is going back to a person regardless; the interesting case is the
            # one somebody signed off on and would not otherwise look at again.
            if only_passed and report.get("overall_verdict") != "PASS":
                continue
            reasons = assess(report, clauses)
            if reasons:
                affected.append(
                    Affected(
                        job_id=str(job.id),
                        filename=job.filename,
                        sha256=job.sha256,
                        reasons=reasons,
                    )
                )
    return affected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include checks that did not pass. By default only passed checks are listed.",
    )
    args = parser.parse_args()

    affected = asyncio.run(find_affected(only_passed=not args.all))

    if args.json:
        print(json.dumps({"affected": [item.as_json() for item in affected]}))
        return

    if not affected:
        print("No stored check is affected by the current corpus.")
        return
    print(f"{len(affected)} affected check(s):")
    for item in affected:
        print(f"  {item.job_id}  {item.filename}")
        for reason in item.reasons:
            print(f"      {reason}")


if __name__ == "__main__":
    main()
