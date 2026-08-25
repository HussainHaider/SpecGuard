"""Rule identity and the per-rule verdict record."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import Field, computed_field, model_validator

from specguard.models.citation import Citation
from specguard.models.common import SpecGuardModel


class RuleId(StrEnum):
    """The eight compliance rules. These strings are stable and appear in stored results."""

    MANDATORY_FIELDS = "MANDATORY_FIELDS"
    NUTRITION_ARITHMETIC = "NUTRITION_ARITHMETIC"
    NUTRITION_PER_100 = "NUTRITION_PER_100"
    ALLERGEN_EMPHASIS = "ALLERGEN_EMPHASIS"
    NUTRITION_CLAIM_CONDITIONS = "NUTRITION_CLAIM_CONDITIONS"
    HEALTH_CLAIM_AUTHORISED = "HEALTH_CLAIM_AUTHORISED"
    ORIGIN_DECLARATION = "ORIGIN_DECLARATION"
    LEGAL_NAME_AND_QUID = "LEGAL_NAME_AND_QUID"


class RuleKind(StrEnum):
    """How a rule reaches its verdict."""

    DETERMINISTIC = "deterministic"
    RAG = "rag"


#: Which rules are pure Python and which retrieve. This is intrinsic to the rule id,
#: not configuration: non-negotiable #2 says a deterministic rule may never become an
#: LLM call, so the mapping lives with the enum and RuleResult validates against it.
RULE_KINDS: Mapping[RuleId, RuleKind] = MappingProxyType(
    {
        RuleId.MANDATORY_FIELDS: RuleKind.DETERMINISTIC,
        RuleId.NUTRITION_ARITHMETIC: RuleKind.DETERMINISTIC,
        RuleId.NUTRITION_PER_100: RuleKind.DETERMINISTIC,
        RuleId.ALLERGEN_EMPHASIS: RuleKind.DETERMINISTIC,
        RuleId.NUTRITION_CLAIM_CONDITIONS: RuleKind.RAG,
        RuleId.HEALTH_CLAIM_AUTHORISED: RuleKind.RAG,
        RuleId.ORIGIN_DECLARATION: RuleKind.RAG,
        RuleId.LEGAL_NAME_AND_QUID: RuleKind.RAG,
    }
)


class Verdict(StrEnum):
    """Outcome of a single rule.

    NEEDS_REVIEW is not a failure mode, it is the designed response to insufficient
    evidence: unreadable input, a low-confidence extraction, or retrieval that did
    not support the judged verdict.
    """

    PASS = "PASS"  # noqa: S105 - a verdict, not a credential
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class AbstentionReason(StrEnum):
    """Why a rule declined to decide. Recorded so abstention rates are measurable."""

    LOW_EXTRACTION_CONFIDENCE = "low_extraction_confidence"
    FIELD_MISSING = "field_missing"
    NO_RELEVANT_CLAUSE_RETRIEVED = "no_relevant_clause_retrieved"
    CITATION_UNVERIFIED = "citation_unverified"
    JUDGE_UNCERTAIN = "judge_uncertain"
    RULE_ERROR = "rule_error"


class LlmUsage(SpecGuardModel):
    """Trace of one model call, per non-negotiable #5."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    latency_ms: int = Field(ge=0)
    langsmith_run_id: str | None = None


class RuleResult(SpecGuardModel):
    """The verdict for one rule against one spec.

    Two of the project's non-negotiables are enforced here rather than left to each
    rule's implementation:

    * A PASS or FAIL requires at least one citation (#1). A rule that cannot cite
      must return NEEDS_REVIEW.
    * A deterministic rule may carry no LLM usage record (#2). If a trace shows up
      on MANDATORY_FIELDS, that is a bug and it fails loudly here.
    """

    rule_id: RuleId
    verdict: Verdict
    citations: list[Citation] = Field(default_factory=list)
    rationale: str = Field(
        min_length=1,
        description="Why this verdict, in reviewer-readable prose. Never the raw model output.",
    )
    suggested_fix: str | None = Field(
        default=None,
        description="Concrete remediation. Required for FAIL, optional otherwise.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    abstention_reason: AbstentionReason | None = None
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description='Numbers behind a deterministic verdict, e.g. {"declared_kcal": 212.0, '
        '"computed_kcal": 198.4, "tolerance_pct": 5.0}. Rendered as the evidence table.',
    )
    llm_usage: list[LlmUsage] = Field(
        default_factory=list,
        description="One entry per model call. Empty for deterministic rules, by construction.",
    )
    duration_ms: int = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind(self) -> RuleKind:
        """Whether this rule is deterministic or RAG-backed. Derived, never stored twice."""
        return RULE_KINDS[self.rule_id]

    @model_validator(mode="after")
    def _enforce_non_negotiables(self) -> Self:
        if self.verdict is not Verdict.NEEDS_REVIEW and not self.citations:
            raise ValueError(
                f"{self.rule_id} returned {self.verdict} without a citation; "
                "a rule that cannot cite must return NEEDS_REVIEW"
            )
        if self.verdict is Verdict.FAIL and not self.suggested_fix:
            raise ValueError(f"{self.rule_id} returned FAIL without a suggested_fix")
        if self.verdict is Verdict.NEEDS_REVIEW and self.abstention_reason is None:
            raise ValueError(f"{self.rule_id} returned NEEDS_REVIEW without an abstention_reason")
        if RULE_KINDS[self.rule_id] is RuleKind.DETERMINISTIC and self.llm_usage:
            raise ValueError(
                f"{self.rule_id} is deterministic but carries an LLM usage record; "
                "deterministic rules must not call a model"
            )
        return self
