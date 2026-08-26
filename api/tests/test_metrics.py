"""Tier 1 metric definitions.

Most of these have more than one defensible reading, and the reading chosen changes what
the build gates on. These tests pin the choice rather than the arithmetic.
"""

from __future__ import annotations

import evals.metrics as metrics
import pytest
from evals.golden import GoldenRetrieval, GoldenRule, Provenance, Split

from specguard.models.citation import Citation
from specguard.models.common import Language
from specguard.models.rule import AbstentionReason, LlmUsage, RuleId, RuleResult, Verdict

PROVENANCE = Provenance(source="generator", labelled_by="test", created_at="2026-08-26")
SOURCE_VERSION = "02011R1169-20180101-en"


def citation() -> Citation:
    return Citation.for_clause(
        regulation="Regulation (EU) No 1169/2011",
        article="9",
        paragraph="1",
        quoted_span="the name of the food shall be its legal name",
        source_version=SOURCE_VERSION,
    )


def golden(
    rule_id: RuleId = RuleId.ORIGIN_DECLARATION,
    expected: Verdict = Verdict.PASS,
    split: Split = Split.DEV,
    spec_id: str = "SPEC-001",
) -> GoldenRule:
    return GoldenRule(
        golden_id=f"GOLD-{spec_id}-{rule_id.value}",
        split=split,
        spec_id=spec_id,
        filename=f"{spec_id}.pdf",
        rule_id=rule_id,
        language=Language.EN,
        expected_verdict=expected,
        provenance=PROVENANCE,
    )


def result(
    rule_id: RuleId = RuleId.ORIGIN_DECLARATION,
    verdict: Verdict = Verdict.PASS,
    *,
    citations: list[Citation] | None = None,
    usage: list[LlmUsage] | None = None,
) -> RuleResult:
    if verdict is Verdict.NEEDS_REVIEW:
        return RuleResult(
            rule_id=rule_id,
            verdict=verdict,
            rationale="not enough evidence",
            confidence=0.3,
            abstention_reason=AbstentionReason.CITATION_UNVERIFIED,
            llm_usage=usage or [],
        )
    return RuleResult(
        rule_id=rule_id,
        verdict=verdict,
        citations=citations if citations is not None else [citation()],
        rationale="because the clause says so",
        suggested_fix="declare it" if verdict is Verdict.FAIL else None,
        confidence=0.9,
        llm_usage=usage or [],
    )


def usage(latency_ms: int = 0, cost_usd: float = 0.0) -> LlmUsage:
    return LlmUsage(
        provider="fake",
        model="recorded",
        prompt_version="judge@v1",
        input_tokens=100,
        output_tokens=10,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


class TestAccuracy:
    def test_an_abstention_counts_as_wrong(self):
        """Declining to answer is a designed outcome. It is not a correct answer."""
        scored = [metrics.Scored(golden(), result(verdict=Verdict.NEEDS_REVIEW))]
        computed = metrics.compute(scored)
        assert computed.accuracy == 0.0
        assert computed.abstention_rate == 1.0
        assert computed.wrong_verdicts == 0

    def test_a_confident_wrong_answer_is_not_an_abstention(self):
        scored = [metrics.Scored(golden(expected=Verdict.FAIL), result(verdict=Verdict.PASS))]
        computed = metrics.compute(scored)
        assert computed.wrong_verdicts == 1
        assert computed.false_passes == 1
        assert computed.abstention_rate == 0.0

    def test_a_false_fail_is_wrong_but_not_a_false_pass(self):
        scored = [metrics.Scored(golden(), result(verdict=Verdict.FAIL))]
        computed = metrics.compute(scored)
        assert computed.wrong_verdicts == 1
        assert computed.false_passes == 0


class TestAllergenFalseNegatives:
    def test_an_abstention_on_an_allergen_failure_is_a_miss(self):
        """Strict on purpose: "needs review" does not tell anyone there is an allergen problem."""
        scored = [
            metrics.Scored(
                golden(rule_id=RuleId.ALLERGEN_EMPHASIS, expected=Verdict.FAIL),
                result(rule_id=RuleId.ALLERGEN_EMPHASIS, verdict=Verdict.NEEDS_REVIEW),
            )
        ]
        computed = metrics.compute(scored)
        assert computed.allergen_fnr == 1.0
        assert computed.allergen_false_passes == 0

    def test_catching_the_failure_scores_zero(self):
        scored = [
            metrics.Scored(
                golden(rule_id=RuleId.ALLERGEN_EMPHASIS, expected=Verdict.FAIL),
                result(rule_id=RuleId.ALLERGEN_EMPHASIS, verdict=Verdict.FAIL),
            )
        ]
        assert metrics.compute(scored).allergen_fnr == 0.0

    def test_a_split_with_no_allergen_failure_has_no_rate(self):
        """None, not zero. Zero reads as "nothing was missed"; nothing was asked."""
        computed = metrics.compute([metrics.Scored(golden(), result())])
        assert computed.allergen_fnr is None
        assert computed.allergen_cases == 0

    def test_mandatory_fields_counts_as_allergen_sensitive(self):
        """It carries the Annex II particulars, and the runtime gate escalates on it too."""
        scored = [
            metrics.Scored(
                golden(rule_id=RuleId.MANDATORY_FIELDS, expected=Verdict.FAIL),
                result(rule_id=RuleId.MANDATORY_FIELDS, verdict=Verdict.PASS),
            )
        ]
        computed = metrics.compute(scored)
        assert computed.allergen_cases == 1
        assert computed.allergen_fnr == 1.0


class TestCitationResolution:
    def test_a_verdict_citing_an_unindexed_clause_does_not_resolve(self):
        scored = [metrics.Scored(golden(), result())]
        assert metrics.compute(scored, known_chunk_ids=set()).citation_resolution_rate == 0.0

    def test_a_resolvable_verdict_scores_one(self):
        scored = [metrics.Scored(golden(), result())]
        known = {citation().chunk_id}
        assert metrics.compute(scored, known_chunk_ids=known).citation_resolution_rate == 1.0

    def test_abstentions_are_excluded(self):
        """An abstention is not required to cite, so counting it would flatter the number."""
        scored = [
            metrics.Scored(golden(), result(verdict=Verdict.NEEDS_REVIEW)),
            metrics.Scored(golden(spec_id="SPEC-002"), result()),
        ]
        computed = metrics.compute(scored, known_chunk_ids={citation().chunk_id})
        assert computed.decided == 1
        assert computed.citation_resolution_rate == 1.0


class TestRetrieval:
    def _record(self, chunk_ids: list[str], split: Split = Split.DEV) -> GoldenRetrieval:
        return GoldenRetrieval(
            golden_id="RET-1",
            split=split,
            rule_id=RuleId.ORIGIN_DECLARATION,
            language=Language.EN,
            query="country of origin",
            search_key="abc123",
            relevant_chunk_ids=chunk_ids,
            relevant_references=["Regulation (EU) No 1169/2011 26(2)"] * len(chunk_ids),
            provenance=PROVENANCE,
        )

    def test_recall_is_the_share_of_relevant_clauses_in_the_top_five(self):
        scored = [metrics.RetrievalScored(self._record(["a", "b"]), ["a", "x", "y"])]
        computed = metrics.compute([], scored)
        assert computed.recall_at_5 == 0.5
        assert computed.hit_rate_at_5 == 1.0

    def test_nothing_below_rank_five_counts(self):
        scored = [metrics.RetrievalScored(self._record(["a"]), ["v", "w", "x", "y", "z", "a"])]
        computed = metrics.compute([], scored)
        assert computed.recall_at_5 == 0.0
        assert computed.hit_rate_at_5 == 0.0

    def test_a_complete_miss_scores_zero(self):
        scored = [metrics.RetrievalScored(self._record(["a"]), ["x"])]
        assert metrics.compute([], scored).recall_at_5 == 0.0


class TestLatencyAndCost:
    def test_a_deterministic_rule_contributes_no_latency_sample(self):
        """It is microseconds of arithmetic; including it would drag the percentiles to zero."""
        rule = RuleId.NUTRITION_PER_100
        scored = [metrics.Scored(golden(rule_id=rule), result(rule))]
        computed = metrics.compute(scored)
        assert computed.latency_samples == 0
        assert computed.p50_latency_ms is None

    def test_a_replayed_call_reports_no_latency(self):
        """A replay has none of its own, and reporting its microseconds would be a lie."""
        scored = [metrics.Scored(golden(), result(usage=[usage(latency_ms=0)]))]
        assert metrics.compute(scored).p50_latency_ms is None

    def test_recorded_latency_is_reported(self):
        scored = [
            metrics.Scored(golden(spec_id=f"SPEC-{n:03d}"), result(usage=[usage(latency_ms=n)]))
            for n in (100, 200, 300, 4000)
        ]
        computed = metrics.compute(scored)
        assert computed.latency_samples == 4
        assert computed.p50_latency_ms == 200
        assert computed.p95_latency_ms == 4000

    def test_cost_per_spec_uses_the_full_check_not_the_sampled_rules(self):
        """The question is what one document costs to check, and half a check does not answer it."""
        scored = [metrics.Scored(golden(), result(usage=[usage(cost_usd=0.001)]))]
        computed = metrics.compute(scored, spec_cost={"SPEC-001": 0.05, "SPEC-999": 9.0})
        assert computed.cost_per_spec_usd == pytest.approx(0.05)


class TestSplits:
    def test_splits_are_reported_separately_and_not_averaged(self):
        scored = [
            metrics.Scored(golden(split=Split.DEV), result()),
            metrics.Scored(
                golden(split=Split.HELD_OUT, spec_id="SPEC-002", expected=Verdict.FAIL),
                result(verdict=Verdict.PASS),
            ),
        ]
        results = metrics.by_split(scored)
        assert results["dev"].accuracy == 1.0
        assert results["held_out"].accuracy == 0.0
        assert results["all"].accuracy == 0.5
