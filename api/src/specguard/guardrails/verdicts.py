"""Gates applied to finished rule results, before a report is allowed to stand.

Each of these can only ever make a report *more* cautious: downgrade a decision to
NEEDS_REVIEW, or mark it for a human. None can turn a NEEDS_REVIEW into a PASS. That
direction is the whole safety argument — a guardrail that could clear a finding would be
a way to lose one.
"""

from __future__ import annotations

from dataclasses import dataclass

from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import ProductSpec

#: Rules whose FAIL is never auto-actionable. An undeclared or unemphasised allergen is
#: the failure mode that puts someone in hospital, so it goes to a person no matter how
#: confident the machine was — including when the machine was confident and right.
ALLERGEN_SENSITIVE: frozenset[RuleId] = frozenset(
    {RuleId.ALLERGEN_EMPHASIS, RuleId.MANDATORY_FIELDS}
)


@dataclass(frozen=True)
class GateOutcome:
    """A result after gating, and what was done to it."""

    result: RuleResult
    notes: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.notes)


def resolve_citations(result: RuleResult, known_chunk_ids: set[str]) -> RuleResult | None:
    """Downgrade a verdict whose citation does not resolve against the index.

    A citation is a promise that a reviewer can go and read the clause. If the chunk id
    is not in the index the promise is empty, and the verdict rests on nothing a person
    can check — which is exactly the failure non-negotiable #1 exists to prevent.
    """
    if result.verdict is Verdict.NEEDS_REVIEW or not result.citations:
        return None

    unresolved = [c for c in result.citations if c.chunk_id not in known_chunk_ids]
    if not unresolved:
        return None

    references = ", ".join(citation.reference for citation in unresolved)
    return RuleResult(
        rule_id=result.rule_id,
        verdict=Verdict.NEEDS_REVIEW,
        rationale=(
            f"{result.rationale} (withheld: the cited clause could not be resolved "
            f"against the indexed corpus — {references})"
        ),
        confidence=result.confidence,
        abstention_reason=AbstentionReason.CITATION_UNVERIFIED,
        metrics=result.metrics,
        llm_usage=result.llm_usage,
        duration_ms=result.duration_ms,
        langsmith_run_id=result.langsmith_run_id,
    )


def force_low_confidence_abstention(result: RuleResult, minimum: float) -> RuleResult | None:
    """Downgrade a decision the rule itself was not confident in.

    A rule that reaches a verdict at 0.3 confidence has told us it is guessing. Reporting
    that as a decision launders a guess into a finding.
    """
    if result.verdict is Verdict.NEEDS_REVIEW or result.confidence >= minimum:
        return None

    return RuleResult(
        rule_id=result.rule_id,
        verdict=Verdict.NEEDS_REVIEW,
        rationale=(
            f"{result.rationale} (withheld: reached at {result.confidence:.2f} "
            f"confidence, below the {minimum:.2f} threshold for an automated verdict)"
        ),
        confidence=result.confidence,
        abstention_reason=AbstentionReason.JUDGE_UNCERTAIN,
        metrics=result.metrics,
        llm_usage=result.llm_usage,
        duration_ms=result.duration_ms,
        langsmith_run_id=result.langsmith_run_id,
    )


def needs_human_review(result: RuleResult) -> bool:
    """Whether this result must be seen by a person before anyone acts on it.

    Note this does not change the verdict. An allergen FAIL is a real finding and
    downgrading it to NEEDS_REVIEW would throw away the very thing that matters; it is
    flagged for a human *and* reported as a failure.
    """
    return result.verdict is Verdict.FAIL and result.rule_id in ALLERGEN_SENSITIVE


def apply_gates(
    result: RuleResult,
    *,
    known_chunk_ids: set[str],
    min_confidence: float,
) -> GateOutcome:
    """Run every verdict gate in order, most objective first."""
    notes: list[str] = []
    current = result

    resolved = resolve_citations(current, known_chunk_ids)
    if resolved is not None:
        notes.append("citation did not resolve against the index")
        current = resolved

    confident = force_low_confidence_abstention(current, min_confidence)
    if confident is not None:
        notes.append(f"confidence {current.confidence:.2f} below {min_confidence:.2f}")
        current = confident

    if needs_human_review(current):
        notes.append("allergen-related failure: routed to human review")

    return GateOutcome(result=current, notes=notes)


def review_required(spec: ProductSpec, results: list[RuleResult]) -> list[RuleId]:
    """Which rules on this report need a person to look at them."""
    del spec
    return [result.rule_id for result in results if needs_human_review(result)]
