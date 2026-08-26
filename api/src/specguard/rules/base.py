"""What a rule is, and what it is given to work with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from specguard.models.citation import Citation
from specguard.models.common import ExtractedField, Language
from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import ProductSpec


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule needs that is not the spec itself."""

    source_version: str
    language: Language = Language.EN
    min_confidence: float = 0.60
    energy_tolerance_pct: float = 5.0

    def cite(
        self, regulation: str, article: str, quoted_span: str, paragraph: str | None = None
    ) -> Citation:
        """Cite a fixed clause.

        Deterministic rules still cite — they just do not retrieve. The clause is known
        in advance, so it is named directly rather than searched for, and the citation
        resolves against the same index a RAG rule's would.
        """
        return Citation.for_clause(
            regulation=regulation,
            article=article,
            paragraph=paragraph,
            quoted_span=quoted_span,
            source_version=self.source_version,
        )


@runtime_checkable
class Rule(Protocol):
    """One compliance check."""

    rule_id: RuleId

    def evaluate(self, spec: ProductSpec, context: RuleContext) -> RuleResult:
        """Return this rule's verdict for the spec."""
        ...


def confident[T](field: ExtractedField[T] | None, context: RuleContext) -> bool:
    """Whether a field was read well enough to decide on.

    A missing field and a badly-read field are different things. This answers only the
    second, so callers have to handle absence themselves rather than conflating "the
    supplier omitted it" with "we could not read it".
    """
    return field is not None and field.confidence >= context.min_confidence


def abstain(
    rule_id: RuleId,
    rationale: str,
    reason: AbstentionReason,
    confidence: float = 0.3,
) -> RuleResult:
    """Decline to decide. Abstention is a designed outcome, not a failure."""
    return RuleResult(
        rule_id=rule_id,
        verdict=Verdict.NEEDS_REVIEW,
        rationale=rationale,
        confidence=confidence,
        abstention_reason=reason,
    )
