"""What a rule is, and what it is given to work with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from specguard.corpus.sources import source_version_for
from specguard.llm.protocol import LLMClient
from specguard.models.citation import Citation
from specguard.models.common import ExtractedField, Language
from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import ProductSpec
from specguard.vectorstore.protocol import VectorStore


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
        try:
            source_version = source_version_for(regulation, self.language)
        except KeyError:
            source_version = self.source_version
        return Citation.for_clause(
            regulation=regulation,
            article=article,
            paragraph=paragraph,
            quoted_span=quoted_span,
            source_version=source_version,
        )


@dataclass(frozen=True)
class RagContext(RuleContext):
    """A RuleContext that also carries retrieval and a model client.

    Deterministic rules take a plain RuleContext, which has neither. That is the point:
    non-negotiable #2 is enforced by what the rule is handed, so a deterministic rule has
    nothing to make a model call *with*, however carelessly someone edits it later.
    """

    store: VectorStore = None  # type: ignore[assignment]
    client: LLMClient = None  # type: ignore[assignment]
    retrieval_limit: int = 5
    min_retrieval_score: float = 0.35


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
