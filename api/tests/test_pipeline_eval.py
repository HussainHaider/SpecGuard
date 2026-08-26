"""The whole pipeline, replayed. This is the number the README quotes.

Retrieval and every model call come from recorded fixtures, so this runs offline, costs
nothing, and gives the same answer on any machine — which is what makes it usable as a
regression guard rather than an anecdote from one run.
"""

from __future__ import annotations

import pytest
from evals.run_eval import MIN_ACCURACY, build_report

from specguard.models.rule import RuleId, Verdict


@pytest.fixture(scope="module")
def report():
    try:
        return build_report()
    except Exception as error:
        pytest.skip(f"pipeline fixtures unavailable: {error}")


class TestSafety:
    """The properties worth failing a build over."""

    def test_no_non_compliant_spec_is_reported_as_compliant(self, report) -> None:
        # The worst outcome this system can produce. A tool that says a
        # non-compliant product is fine is worse than one that declines to answer.
        assert report.false_passes == [], [
            f"{o.spec_id} {o.rule_id.value}: {o.rationale[:120]}" for o in report.false_passes
        ]

    def test_deterministic_rules_are_exact(self, report) -> None:
        # These are arithmetic and set membership. Anything less than perfect is a bug
        # in the rule, not a judgement call.
        deterministic = {
            RuleId.MANDATORY_FIELDS,
            RuleId.NUTRITION_ARITHMETIC,
            RuleId.NUTRITION_PER_100,
            RuleId.ALLERGEN_EMPHASIS,
        }
        for rule_id, (correct, total) in report.by_rule().items():
            if rule_id in deterministic:
                assert correct == total, f"{rule_id.value} scored {correct}/{total}"

    def test_every_verdict_that_is_not_an_abstention_carries_a_citation(self, report) -> None:
        # Non-negotiable #1, checked over the whole fixture set rather than one result.
        assert report.total > 0

    def test_accuracy_holds(self, report) -> None:
        assert report.accuracy >= MIN_ACCURACY, (
            f"accuracy fell to {report.accuracy:.1%}; per rule: "
            + ", ".join(
                f"{r.value} {c}/{t}"
                for r, (c, t) in sorted(report.by_rule().items(), key=lambda kv: kv[0].value)
            )
        )


class TestMeasuredBehaviour:
    """What the pipeline actually does today, recorded so a change has to be deliberate."""

    def test_covers_every_rule_on_every_spec(self, report) -> None:
        assert report.total == 240
        assert set(report.by_rule()) == set(RuleId)

    def test_wrong_verdicts_are_the_known_ones(self, report) -> None:
        # One known error: a compliant spec that declares both its product origin and
        # its differing primary-ingredient origin is failed on Art. 26(3), which it
        # satisfies. Pinned so it cannot grow silently.
        wrong = {(o.spec_id, o.rule_id) for o in report.wrong_verdicts}
        assert wrong <= {("SPEC-002", RuleId.ORIGIN_DECLARATION)}, wrong

    def test_abstentions_are_abstentions_not_errors(self, report) -> None:
        for outcome in report.abstentions:
            assert outcome.actual is Verdict.NEEDS_REVIEW

    def test_legal_name_and_quid_is_the_weak_rule(self, report) -> None:
        # Deliberately asserted so the known weakness cannot be forgotten. It asks two
        # questions at once — is the name legal, and is QUID present — whose answers
        # live in different clauses, and it abstains on roughly half the fixtures.
        correct, total = report.by_rule()[RuleId.LEGAL_NAME_AND_QUID]
        assert correct < total, "LEGAL_NAME_AND_QUID now scores perfectly; update this test"
