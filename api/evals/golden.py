"""The golden set: the single source of truth for tier 1.

Two files under ``evals/golden/``, both JSONL, both committed:

* ``rules.jsonl`` — what verdict each rule should reach on each specification.
* ``retrieval.jsonl`` — what a query should retrieve, for recall@5.

They are split because they answer different questions and are labelled differently. A
verdict label comes from a defect the generator deliberately seeded, so it is exact and
mechanically derived. A retrieval label is a judgement about which clause decides a
question, and pretending the two have the same provenance would misrepresent the weaker
one. Keeping recall@5 out of the verdict file also stops a retrieval label drifting to
match whatever the pipeline happened to return.

Both files are read, never written, by anything that measures. ``build_golden.py``
regenerates them and is run deliberately; the diff it produces is the review.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from specguard.models.common import Language, SpecGuardModel
from specguard.models.rule import RuleId, Verdict

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
RULES_PATH = GOLDEN_DIR / "rules.jsonl"
RETRIEVAL_PATH = GOLDEN_DIR / "retrieval.jsonl"


class Split(StrEnum):
    """Which half of the set a record belongs to.

    Assigned per specification, never per record: two rules over the same spec share an
    extraction and a document, so splitting between them would put the same evidence on
    both sides of the line and make the held-out number a re-run of the dev one.
    """

    DEV = "dev"
    HELD_OUT = "held_out"


class Provenance(SpecGuardModel):
    """Where a label came from. Required on every record.

    The rule is that a label may never originate from this system's own output. A
    verdict the pipeline produced, fed back in as ground truth, measures only that the
    pipeline is consistent with itself.
    """

    source: str = Field(
        min_length=1,
        description='"generator" for a seeded defect, "human" for a reviewer correction.',
    )
    labelled_by: str = Field(min_length=1, description="How the label was decided.")
    created_at: str = Field(min_length=1, description="ISO date the record was written.")
    spec_sha256: str | None = Field(
        default=None,
        description="The PDF this label describes. A regenerated document that no longer "
        "hashes to this invalidates the record loudly rather than silently mislabelling it.",
    )
    manifest_sha256: str | None = Field(
        default=None, description="The manifest the label was derived from."
    )
    corpus_version: str | None = Field(
        default=None, description="Corpus the chunk ids resolve against."
    )


class SeededDefect(SpecGuardModel):
    """The deliberate non-compliance a FAIL label rests on."""

    kind: str
    detail: str


class GoldenRule(SpecGuardModel):
    """One rule's expected verdict on one specification."""

    golden_id: str = Field(min_length=1)
    split: Split
    spec_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    rule_id: RuleId
    language: Language
    expected_verdict: Verdict
    defect: SeededDefect | None = None
    adversarial: bool = False
    provenance: Provenance


class GoldenRetrieval(SpecGuardModel):
    """One query and the clauses that should come back for it."""

    golden_id: str = Field(min_length=1)
    split: Split
    rule_id: RuleId
    language: Language
    query: str = Field(min_length=1)
    search_key: str = Field(
        min_length=1,
        description="Identifies the recorded search this query replays, so the offline "
        "path and the live path are measured on the same query.",
    )
    relevant_chunk_ids: list[str] = Field(min_length=1)
    relevant_references: list[str] = Field(
        min_length=1,
        description="The same clauses, human-readable. A chunk id is an opaque UUID and "
        "a golden file nobody can read is a golden file nobody will check.",
    )
    spec_ids: list[str] = Field(
        default_factory=list,
        description="Specifications that issue this query. Several specs can produce one "
        "query, which is why retrieval records are deduplicated by search key.",
    )
    provenance: Provenance


def _read(path: Path) -> Iterator[dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing — build it with `python -m evals.build_golden`")
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_rules(path: Path = RULES_PATH) -> list[GoldenRule]:
    """Every verdict record."""
    return [GoldenRule.model_validate(row) for row in _read(path)]


def load_retrieval(path: Path = RETRIEVAL_PATH) -> list[GoldenRetrieval]:
    """Every retrieval record."""
    return [GoldenRetrieval.model_validate(row) for row in _read(path)]


def write_jsonl(path: Path, records: list[GoldenRule] | list[GoldenRetrieval]) -> None:
    """Write records as JSONL, one per line, stably ordered by id."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        for record in sorted(records, key=lambda r: r.golden_id)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
