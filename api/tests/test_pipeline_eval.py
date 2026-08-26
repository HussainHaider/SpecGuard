"""The whole pipeline, replayed against the golden set. This is the number the README quotes.

Retrieval and every model call come from recorded fixtures, so this runs offline, costs
nothing, and gives the same answer on any machine — which is what makes it usable as a
regression guard rather than an anecdote from one run.

It asserts on the same code path CI runs. A test that recomputed the numbers its own way
would pass while the thing that gates the build was broken.
"""

from __future__ import annotations

import pytest
from evals.golden import Split
from evals.run_eval import build_stores, check_gates, run, score_rules

from specguard.config import get_settings
from specguard.models.rule import RULE_KINDS, RuleId, RuleKind, Verdict


@pytest.fixture(scope="module")
def scored():
    try:
        settings = get_settings()
        store, client = build_stores(settings, live=False)
        return score_rules(store, client, settings)
    except Exception as error:
        pytest.skip(f"pipeline fixtures unavailable: {error}")


@pytest.fixture(scope="module")
def results():
    try:
        return run()
    except Exception as error:
        pytest.skip(f"pipeline fixtures unavailable: {error}")


class TestSafety:
    """The properties worth failing a build over."""

    def test_the_committed_gates_pass(self, results) -> None:
        assert check_gates(results["all"]) == []

    def test_no_non_compliant_spec_is_reported_as_compliant(self, scored) -> None:
        # The worst outcome this system can produce. A tool that says a non-compliant
        # product is fine is worse than one that declines to answer.
        false_passes = [item for item in scored if item.false_pass]
        assert false_passes == [], [item.golden.golden_id for item in false_passes]

    def test_no_allergen_failure_is_missed(self, results) -> None:
        # Strict: an abstention counts as a miss. A reviewer told "needs review" has not
        # been told there is an undeclared allergen.
        for split in ("all", "dev", "held_out"):
            assert results[split].allergen_fnr == 0.0, split

    def test_deterministic_rules_are_exact(self, results) -> None:
        # Arithmetic and set membership. Anything less than perfect is a bug in the rule,
        # not a judgement call.
        for rule_id, (correct, total) in results["all"].per_rule.items():
            if RULE_KINDS[rule_id] is RuleKind.DETERMINISTIC:
                assert correct == total, f"{rule_id.value} scored {correct}/{total}"

    def test_every_decided_verdict_cites_a_resolvable_clause(self, results) -> None:
        # Non-negotiable #1, as a number over the whole golden set.
        assert results["all"].citation_resolution_rate == 1.0


class TestMeasuredBehaviour:
    """What the pipeline actually does today, recorded so a change has to be deliberate."""

    def test_it_scores_the_whole_golden_set(self, results) -> None:
        assert results["all"].records == 80
        assert set(results["all"].per_rule) == set(RuleId)

    def test_both_splits_are_reported_and_neither_is_empty(self, results) -> None:
        for split in Split:
            assert results[split.value].records > 0

    def test_wrong_verdicts_stay_at_zero(self, results) -> None:
        # The one known error — a compliant spec failed on Art. 26(3) — is not among the
        # pairs the golden set samples. Pinned so it cannot reappear unnoticed.
        assert results["all"].wrong_verdicts == 0

    def test_abstentions_are_abstentions_not_errors(self, scored) -> None:
        for item in scored:
            if item.abstained:
                assert item.actual is Verdict.NEEDS_REVIEW
                assert item.result.abstention_reason is not None

    def test_legal_name_and_quid_is_the_weak_rule(self, results) -> None:
        # Deliberately asserted so the known weakness cannot be forgotten. It asks two
        # questions at once — is the name legal, and is QUID present — whose answers live
        # in different clauses, and it abstains on roughly half the fixtures.
        correct, total = results["all"].per_rule[RuleId.LEGAL_NAME_AND_QUID]
        assert correct < total, "LEGAL_NAME_AND_QUID now scores perfectly; update this test"

    def test_retrieval_is_scored_and_imperfect(self, results) -> None:
        # Reported, never gating: the anchors are a judgement about which clause decides
        # a question, and a build should not fail on a labelling opinion.
        assert results["all"].retrieval_queries == 58
        assert 0.0 < results["all"].recall_at_5 < 1.0

    def test_a_replayed_run_reports_no_latency(self, results) -> None:
        # It has none of its own. Reporting the replay's microseconds would claim this
        # system answers instantly.
        assert results["all"].p50_latency_ms is None

    def test_cost_is_real_money_from_recorded_tokens(self, results) -> None:
        assert results["all"].cost_per_spec_usd > 0.0
