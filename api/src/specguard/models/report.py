"""CheckReport: everything one compliance run produced, in one serialisable record."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Self

from pydantic import Field, computed_field, model_validator

from specguard.models.common import SpecGuardModel
from specguard.models.rule import RuleId, RuleResult, Verdict
from specguard.models.spec import ProductSpec


class GuardrailFlags(SpecGuardModel):
    """What the guardrail nodes observed about this document.

    Supplier PDFs are untrusted input (non-negotiable #4). Anything that looked like
    an instruction aimed at the model is recorded here as data and surfaced to the
    reviewer; it is never acted on.
    """

    injection_suspected: bool = False
    injection_signals: list[str] = Field(
        default_factory=list,
        description="Verbatim spans that tripped the injection screen, quoted for the reviewer.",
    )
    low_confidence_fields: list[str] = Field(
        default_factory=list,
        description="Dotted paths of extracted fields below the confidence floor.",
    )
    unreadable_pages: list[int] = Field(default_factory=list)


class CheckReport(SpecGuardModel):
    """The deliverable: one spec, eight rule results, and the versions that produced them.

    The version fields are not decoration. A report is only reproducible if you know
    which corpus snapshot the citations point into and which graph produced them —
    and ``corpus_version`` is what every stored ``chunk_id`` was derived from.
    """

    report_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    job_id: uuid.UUID | None = None
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))

    spec: ProductSpec
    results: list[RuleResult]
    guardrails: GuardrailFlags = Field(default_factory=GuardrailFlags)

    demo: bool = Field(
        default=False,
        description="This report was replayed from a stored fixture, not computed now. "
        "Carried on the report itself so every consumer sees it — a replayed result that "
        "looks live is a lie told to whoever is evaluating the system.",
    )
    demo_note: str | None = None

    corpus_version: str = Field(
        min_length=1,
        description="Corpus snapshot the citations resolve against, e.g. '2024-11-01'.",
    )
    graph_version: str = Field(min_length=1, description="Version of the check graph that ran.")
    duration_ms: int = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overall_verdict(self) -> Verdict:
        """Worst verdict across the rules: any FAIL fails, any abstention needs review."""
        verdicts = {r.verdict for r in self.results}
        if Verdict.FAIL in verdicts:
            return Verdict.FAIL
        if Verdict.NEEDS_REVIEW in verdicts:
            return Verdict.NEEDS_REVIEW
        return Verdict.PASS

    @computed_field  # type: ignore[prop-decorator]
    @property
    def counts(self) -> dict[Verdict, int]:
        """Verdict tally, always with all three keys present so the UI needs no defaulting."""
        tally = dict.fromkeys(Verdict, 0)
        for result in self.results:
            tally[result.verdict] += 1
        return tally

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cost_usd(self) -> float:
        """Cost of every model call in this run, per non-negotiable #5."""
        return sum(usage.cost_usd for r in self.results for usage in r.llm_usage)

    def result_for(self, rule_id: RuleId) -> RuleResult | None:
        """The result for one rule, or None if that rule did not run."""
        return next((r for r in self.results if r.rule_id is rule_id), None)

    @model_validator(mode="after")
    def _one_result_per_rule(self) -> Self:
        seen = [r.rule_id for r in self.results]
        duplicates = {rule_id for rule_id in seen if seen.count(rule_id) > 1}
        if duplicates:
            names = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate rule results in report: {names}")
        return self
